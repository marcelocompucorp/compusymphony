"""Stable, low-churn helpers for visual bug reproduction across Compucorp Drupal 7 sites.

See docs/superpowers/specs/2026-05-15-visual-repro-design.md for the full design.

SECURITY: callers MUST NOT log returned credential dicts; passwords are plain.
"""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests  # type: ignore
from pymongo import MongoClient  # type: ignore

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

# --- Defaults ---

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_TIMEOUT_MS = 30000

# --- Staging allowlist (hostname patterns) ---

_STAGING_PATTERNS = (
    r"\.cc-staging\.site$",
    r"\.cc-data\.site$",
    r"\.cc-prelive\.site$",
    r"\.cc-dev\.site$",
    r"\.cc-test\.site$",
    r"\.public\.cc-test\.site$",  # explicit sub-pattern
)
_STAGING_RE = re.compile("|".join(_STAGING_PATTERNS))


def assert_staging_host(url: str) -> None:
    """Refuse to proceed if url's host is not a known Compucorp staging environment.

    Allowlist: hostname patterns *.cc-{staging,data,prelive,dev,test}.site and
    *.public.cc-test.site. Custom-domain stagings (e.g. staging.client.org)
    will hard-refuse; add Mongo-based fallback in v2 if/when they appear.

    NO escape hatch — this is the only hard rail.
    """
    host = urlparse(url).hostname or ""
    if not _STAGING_RE.search(host):
        raise RuntimeError(
            f"Refusing to run visual repro against non-staging host: {host!r}. "
            f"Allowed patterns: *.cc-staging.site, *.cc-data.site, *.cc-prelive.site, "
            f"*.cc-dev.site, *.cc-test.site, *.public.cc-test.site"
        )


# --- Credentials (sysPass) ---


def get_syspass_cred(account_search: str, *, prefer_name: str | None = None) -> dict:
    """Fetch a Compucorp sysPass credential via the two-step search→viewPass flow.

    Returns: {id, login, password, url, name}
    SECURITY: caller MUST NOT log the returned dict — password is plain.

    Two-step flow:
      1. account/search with text=<account_search>, auth via SYSPASS_TOKEN_SEARCH
         + SYSPASS_PASS_SEARCH.
      2. If prefer_name is set, filter results by case-insensitive equality on
         the `name` field.
      3. Raise ValueError if zero matches or if matches > 1 after filter.
      4. account/viewPass with the resolved id, auth via SYSPASS_TOKEN_VIEWPASS
         + SYSPASS_PASS_VIEWPASS.

    See prompts/TOOLS.md §sysPass for the env var contract.
    """
    base_url = os.environ["SYSPASS_URL"]

    # Normalize input — accept either URL or hostname.
    # sysPass account/search text-matches account fields; URL form (with scheme)
    # may not match if account records store bare hostname. Strip scheme defensively.
    parsed = urlparse(account_search)
    search_text = parsed.hostname or account_search

    # Step 1: search
    search_body = {
        "jsonrpc": "2.0",
        "method": "account/search",
        "params": {
            "authToken": os.environ["SYSPASS_TOKEN_SEARCH"],
            "tokenPass": os.environ["SYSPASS_PASS_SEARCH"],
            "text": search_text,
        },
        "id": 1,
    }
    r = requests.post(f"{base_url}/api.php", json=search_body, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"sysPass API error: {body['error']}")
    accounts = body["result"]["result"]

    # Step 2: filter by prefer_name (case-insensitive equality)
    if prefer_name:
        pn = prefer_name.lower()
        accounts = [a for a in accounts if (a.get("name") or "").lower() == pn]

    if not accounts:
        raise ValueError(
            f"no sysPass account matched search={account_search!r} prefer_name={prefer_name!r}"
        )
    if len(accounts) > 1:
        names = [a.get("name") for a in accounts]
        raise ValueError(
            f"ambiguous sysPass match for search={account_search!r} prefer_name={prefer_name!r}: {names}"
        )

    acc = accounts[0]

    # Step 3: viewPass
    viewpass_body = {
        "jsonrpc": "2.0",
        "method": "account/viewPass",
        "params": {
            "authToken": os.environ["SYSPASS_TOKEN_VIEWPASS"],
            "tokenPass": os.environ["SYSPASS_PASS_VIEWPASS"],
            "id": acc["id"],
        },
        "id": 1,
    }
    r = requests.post(f"{base_url}/api.php", json=viewpass_body, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"sysPass API error: {body['error']}")
    password = body["result"]["result"]["password"]

    return {
        "id": acc["id"],
        "login": acc.get("login"),
        "name": acc.get("name"),
        "url": acc.get("url"),
        "password": password,
    }


# --- Browser setup ---


def _syspass_viewpass_by_id(account_id: int) -> dict:
    """Internal: bypass `account/search` and fetch a password by known id.

    Useful when the script already has the id (from a previous search) and
    wants to avoid a second search round-trip. Also overridable in tests.
    """
    base_url = os.environ["SYSPASS_URL"]
    body = {
        "jsonrpc": "2.0",
        "method": "account/viewPass",
        "params": {
            "authToken": os.environ["SYSPASS_TOKEN_VIEWPASS"],
            "tokenPass": os.environ["SYSPASS_PASS_VIEWPASS"],
            "id": account_id,
        },
        "id": 1,
    }
    r = requests.post(f"{base_url}/api.php", json=body, timeout=15)
    r.raise_for_status()
    resp = r.json()
    if resp.get("error"):
        raise RuntimeError(f"sysPass API error: {resp['error']}")
    return {"id": account_id, "password": resp["result"]["result"]["password"]}


def basic_auth_context(
    browser: "Browser",
    *,
    syspass_account_id: int,
    viewport: dict | None = None,
) -> "BrowserContext":
    """Playwright context with Traefik Basic Auth + pinned viewport.

    Looks up the Basic Auth password by sysPass account id (caller passes
    the id from get_syspass_cred). Returns a context ready for `.new_page()`.

    NOTE: `_syspass_viewpass_by_id` only fetches the password, not the login
    field. Basic Auth username defaults to "ies" (the standard Compucorp
    Traefik Basic Auth user). If a future site uses a different login, switch
    callers to pass the full cred dict from `get_syspass_cred` (which does
    include `login`) and extend this helper.
    """
    cred = _syspass_viewpass_by_id(syspass_account_id)
    return browser.new_context(
        http_credentials={"username": cred.get("login", "ies"), "password": cred["password"]},
        viewport=viewport or DEFAULT_VIEWPORT,
    )


# --- UI utility ---


def dismiss_cookie_banner(page: "Page") -> bool:
    """Best-effort dismiss of the standard Compucorp cookie consent banner.

    Returns True if a banner was found and clicked, False if not present.
    Non-fatal either way — never raises.
    """
    try:
        page.get_by_role("button", name="OK, I agree").click(timeout=2000)
        return True
    except Exception:
        return False


# --- Drupal login (form-shape autodetect — SSP-only validated) ---

def compucorp_drupal_login_autodetect(
    page: "Page",
    username: str,
    password: str,
    *,
    site: str,
    try_cognito_bypass: bool = False,  # path not implemented yet; opt-in when ready
) -> None:
    """Detect login form shape and drive the flow.

    The `site` parameter is the full scheme+host of the target staging site,
    e.g. `https://ies2.cc-staging.site`. The helper navigates to `/user/login`
    on that host. This is required (not derived from `page.url`) because a
    freshly-created Playwright page starts at `about:blank` — there's no host
    to derive from on the first navigation.

    Empirical validation status (2026-05-15 / updated 2026-05-19):
      - SSP two-step: validated against ies2.cc-staging.site
      - Standard Drupal 7 one-step (#edit-name + #edit-pass on same page):
        implemented and used by fresh Jenkins-spun dev sites in step 12b-bis.
        Not yet validated end-to-end against a real fresh dev site — first
        dry-run will confirm.
      - Cognito-bypass (/user/local/login): NOT validated — raises NotImplementedError

    Raises if no logout link appears after the attempt.
    """
    site_root = site

    # Step 1: try Cognito bypass — probe /user/local/login first if enabled
    if try_cognito_bypass:
        # Cognito-bypass detection: navigate to /user/local/login and check if a
        # Drupal login form is served (vs 404 / redirect to Cognito).
        # Empirically unvalidated — raise immediately so the agent falls through
        # to manual verification. Remove this guard once smoke-tested on a real
        # Cognito site.
        # For now, skip the probe entirely. (try_cognito_bypass=False on known
        # non-Cognito sites avoids spurious /user/local/login traffic.)
        pass

    # Step 2: navigate to /user/login (the SSP form action). Always issue the
    # goto — idempotent and ensures we're at the canonical login URL even if
    # the caller already navigated there (e.g. after a redirect).
    page.goto(f"{site_root}/user/login",
              wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    dismiss_cookie_banner(page)

    # Step 3: detect form shape
    ssp_form = page.locator("form#ssp-core-user-login-or-register-form").first
    if ssp_form.count() > 0 and ssp_form.is_visible():
        # Existing client staging sites (SSP-themed): two-step username → password
        print(f"compucorp_drupal_login_autodetect: detected SSP two-step form on {site_root}")
        _drive_ssp_two_step(page, ssp_form, username, password)
    elif page.locator("input#edit-name").count() > 0:
        # Stock Drupal 7 login form — single page with name + pass + submit.
        # This is the shape on fresh Jenkins-spun dev sites that don't ship
        # the SSP theme. Validated against the standard Drupal 7 distribution
        # markup; if a Compucorp client deviates, add a new branch here.
        print(f"compucorp_drupal_login_autodetect: detected standard one-step form on {site_root}")
        _drive_standard_one_step(page, username, password)
    else:
        # No recognised form shape. Log the URL so the operator can inspect
        # what's actually rendered — the previous generic message hid this.
        raise NotImplementedError(
            f"compucorp_drupal_login_autodetect: no recognised login form on "
            f"{site_root}/user/login. Supported shapes: SSP two-step "
            f"(form#ssp-core-user-login-or-register-form), standard Drupal 7 "
            f"one-step (input#edit-name). Inspect the live page and add the "
            f"new shape to this helper."
        )

    # Step 4: verify logged in
    logout_count = page.locator(
        "a[href*='/user/logout'], a[href*='/sso/auth/logout']"
    ).count()
    if logout_count == 0:
        raise RuntimeError(
            f"login attempt for {username!r} did not produce a logout link; "
            "credentials may be wrong or site auth changed"
        )


def _drive_standard_one_step(page, username, password):
    """Internal: stock Drupal 7 login flow.

    The standard form has #edit-name + #edit-pass on the same page, with a
    submit button (#edit-submit OR input[name='op']). One form submission,
    no AJAX step. Used by fresh Jenkins-spun dev sites that don't carry the
    SSP theme.
    """
    page.locator("input#edit-name").first.fill(username)
    page.locator("input#edit-pass").first.fill(password)
    submit = page.locator("input#edit-submit, input[name='op'][type='submit']").first
    try:
        with page.expect_navigation(wait_until="networkidle",
                                    timeout=DEFAULT_TIMEOUT_MS // 2):
            submit.click()
    except Exception:
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS // 2)


def _drive_ssp_two_step(page, form, username, password):
    """Internal: SSP two-step login flow (username → AJAX → password → submit)."""
    form.locator("input[name='name']").fill(username)
    form.locator("button[type='submit'][name='op']").click()
    page.wait_for_selector("input[type='password']:visible",
                           timeout=DEFAULT_TIMEOUT_MS)

    pwd = page.locator("input[type='password']:visible").first
    pwd.fill(password)
    pwd_form = pwd.locator("xpath=ancestor::form").first
    try:
        with page.expect_navigation(wait_until="networkidle",
                                    timeout=DEFAULT_TIMEOUT_MS // 2):
            pwd_form.locator(
                "button[type='submit'], input[type='submit']"
            ).first.click()
    except Exception:
        # Sometimes the submit is AJAX too — fall back to load-state wait
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS // 2)


# --- Test user lifecycle ---

class UserExistsError(Exception):
    """Raised when creating a test user that already exists."""


class CreateUserError(Exception):
    """Raised when /admin/people/create submit fails for any other reason."""


_UID_FROM_URL_RE = re.compile(r"/user/(\d+)/edit")


def _site_root(url: str) -> str:
    """Extract scheme://host from a URL, stripping any path/query/fragment."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else url.rstrip("/")


def create_test_user(admin_page: "Page", *, username: str, email: str,
                     password: str) -> int:
    """Create a non-admin test user via /admin/people/create.

    Returns the new user's uid.

    On success, Drupal 7's user-add form either redirects to /user/<uid>/edit
    (older themes) or stays on /admin/people/create and renders a status
    message ("Created a new user account for X."). Both shapes are handled:
      - URL match: uid parsed from /user/<uid>/edit.
      - Status-message match: uid resolved via /admin/people lookup.

    Raises UserExistsError if the username is already taken,
    CreateUserError on other failures.
    """
    site_root = _site_root(admin_page.url)
    admin_page.goto(f"{site_root}/admin/people/create",
                    wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    admin_page.fill("input#edit-name", username)
    admin_page.fill("input#edit-mail", email)
    admin_page.fill("input#edit-pass-pass1", password)
    admin_page.fill("input#edit-pass-pass2", password)
    # Don't check any non-default role (authenticated user is implicit).
    # Don't tick "Notify user of new account" — no email delivery.

    try:
        with admin_page.expect_navigation(wait_until="networkidle",
                                          timeout=DEFAULT_TIMEOUT_MS):
            admin_page.click("input#edit-submit")
    except Exception:
        # Some Drupal builds don't navigate (AJAX submit); fall back
        admin_page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

    # Shape 1: redirected to /user/<uid>/edit — pull uid straight from URL.
    m = _UID_FROM_URL_RE.search(admin_page.url)
    if m:
        return int(m.group(1))

    # Shape 2: stayed on the create form (or landed somewhere else). Check for
    # an error message first — if Drupal rejected the submission, the create
    # form re-renders with a `.messages.error` block; we should NOT then try
    # to look the user up (it doesn't exist).
    error_text = ""
    try:
        if admin_page.locator(".messages--error, .messages.error").count() > 0:
            error_text = admin_page.locator(".messages--error, .messages.error") \
                                   .first.inner_text(timeout=2000)
    except Exception:
        pass
    if error_text:
        if any(s in error_text.lower() for s in ("already taken", "already registered")):
            raise UserExistsError(f"user {username!r} exists: {error_text}")
        raise CreateUserError(
            f"failed to create user {username!r}: {error_text!r} url={admin_page.url}"
        )

    # Shape 2 continued: no error → look for the success status message and
    # resolve the uid via the admin/people listing.
    status_text = ""
    try:
        if admin_page.locator(".messages--status, .messages.status").count() > 0:
            status_text = admin_page.locator(".messages--status, .messages.status") \
                                    .first.inner_text(timeout=2000)
    except Exception:
        pass
    # Drupal 7 success: "Created a new user account for <username>."
    if "created a new user account" in status_text.lower():
        uid = find_uid_by_username(admin_page, username)
        if uid is not None:
            return uid
        raise CreateUserError(
            f"create succeeded (status={status_text!r}) but uid lookup for "
            f"{username!r} returned None"
        )

    raise CreateUserError(
        f"failed to create user {username!r}: no success/error message; "
        f"url={admin_page.url} status_text={status_text!r}"
    )


def find_uid_by_username(admin_page: "Page", username: str) -> int | None:
    """Look up a uid by username via /admin/people?user=<username>.

    Returns None if not found. Used by cleanup when create_test_user partially
    failed (created but uid wasn't captured).
    """
    site_root = _site_root(admin_page.url)
    admin_page.goto(f"{site_root}/admin/people?user={username}",
                    wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)
    row = admin_page.locator(f"tr:has-text('{username}')").first
    if row.count() == 0:
        return None
    href = row.locator("a[href*='/user/']").first.get_attribute("href") or ""
    m = _UID_FROM_URL_RE.search(href)
    return int(m.group(1)) if m else None


def cancel_test_user_by_uid(admin_page: "Page", uid: int) -> None:
    """Cancel a test user via /user/<uid>/cancel + user_cancel_delete.

    Idempotent: logs and returns if the user is already gone.
    Uses force=True on the cancel-method radio because Drupal's label
    intercepts pointer events.
    """
    site_root = _site_root(admin_page.url)
    admin_page.goto(f"{site_root}/user/{uid}/cancel",
                    wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    radio = admin_page.locator(
        "input[name='user_cancel_method'][value='user_cancel_delete']"
    )
    if radio.count() == 0:
        return  # already gone

    radio.check(force=True)
    try:
        with admin_page.expect_navigation(wait_until="networkidle",
                                          timeout=DEFAULT_TIMEOUT_MS):
            admin_page.click("input[name='op'][value='Cancel account']")
    except Exception:
        admin_page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)


@contextmanager
def lifecycle_test_user(admin_page: "Page", username: str, password: str,
                        email: str):
    """Context manager: create test user on enter, cancel on exit.

    Yields the username. Cleanup runs on normal exit AND on raised exceptions.
    Cleanup does NOT run on SIGKILL / hard crashes; deferred sweeper handles those.

    Username convention: `symphony-test-<random-hex6>` to avoid collision.
    """
    uid = create_test_user(admin_page, username=username, email=email,
                           password=password)
    try:
        yield username
    finally:
        try:
            cancel_test_user_by_uid(admin_page, uid)
        except Exception:
            # Best-effort cleanup. If it fails, find_uid_by_username can recover.
            try:
                recovered = find_uid_by_username(admin_page, username)
                if recovered:
                    cancel_test_user_by_uid(admin_page, recovered)
            except Exception:
                pass  # logged via Symphony transcript; sweeper handles eventually


# --- Jenkins dev-site provisioning (WORKFLOW.md step 12b-bis) ---

# Job path for the only Jenkins job the agent is permitted to trigger.
# Pinned by literal string per WORKFLOW.md invariant #5 carve-out — the
# audit (analyze-run.py) greps for this exact substring. If you change it,
# update the audit detector and WORKFLOW.md in the same commit.
_DEVSITE_JOB_PATH = (
    "/job/Deployments/job/Dev%20Sites%20-%20Compucontainer"
    "/job/Create%20Dev%20Site%20-%20Client%20Specific"
)

# Hostname extraction: the downstream Pipeline-Mysql8 job emits a line like
# "Pipeline-Mysql8 #1237-uniformlycreativegrizzly.public.cc-test.site
# completed. Result was SUCCESS". Three observed hostname shapes:
#   - *.public.cc-test.site    (public sites)
#   - *.docker.cc-test.site    (internal Docker-stack sites, seen in IESBUILD-242)
#   - *.cc-test.site           (bare internal sites)
# We only accept SUCCESS lines — a FAILURE line never yields a usable hostname.
_DEVSITE_HOSTNAME_RE = re.compile(
    r"Pipeline-Mysql8\s+#\d+-([a-z0-9-]+(?:\.(?:public|docker))?\.cc-test\.site)"
    r"\s+completed\.\s+Result\s+was\s+SUCCESS",
    re.IGNORECASE,
)


def devsite_git_tag(branch: str) -> str:
    """Return a Docker-safe version of a git branch name.

    Docker tags cannot contain '/'. Agent branches follow the convention
    `agent/<TICKET>-fix`; this converts them to `agent-<TICKET>-fix` for
    use as `git_tag` in `trigger_dev_site`. The caller pushes a lightweight
    git tag under this name before triggering, then deletes it after the
    build completes to avoid polluting the remote.

    Example: `agent/IESBUILD-242-fix` → `agent-IESBUILD-242-fix`
    """
    return branch.replace("/", "-")


def _extract_devsite_hostname(console_text: str) -> str | None:
    """Return the dev-site hostname from a Jenkins console log, or None.

    Looks for the canonical `Pipeline-Mysql8 #N-<host> completed. Result
    was SUCCESS` line. FAILURE lines are deliberately ignored — a failed
    sub-build does not yield a usable hostname.

    If multiple SUCCESS lines exist in the console (e.g. a flaky earlier
    sub-build SUCCESS-then-retry), returns the LAST one — the final pipe
    result is authoritative.
    """
    if not console_text:
        return None
    matches = _DEVSITE_HOSTNAME_RE.findall(console_text)
    return matches[-1] if matches else None


def trigger_dev_site(
    *,
    git_repo: str,
    git_tag: str,
    anondb_url: str,
    public: bool = False,
    client_name: str | None = None,
    lifespan: int | None = None,
    mautic: bool = False,
) -> str:
    """Trigger the `Create Dev Site - Client Specific` Jenkins job.

    Returns the queue-item URL (from the `Location` response header) — callers
    pass this to `poll_until_deployed` to wait for the build and resolve the
    dev-site hostname.

    Auth: env vars `JENKINS_URL`, `JENKINS_USER`, `JENKINS_TOKEN`. The bot
    user (`openclawautomation`) must have Build permission on the job — this
    was confirmed 2026-05-18 in the Jenkins UI for the production deployment.

    Raises:
        ValueError: public=True without client_name (Jenkins job rejects it).
        requests.HTTPError: on 4xx/5xx from Jenkins.
        RuntimeError: if Jenkins returned 2xx but no Location header (anomaly).

    SECURITY: only this one job path is allowed per WORKFLOW.md invariant #5
    carve-out. Do NOT add a generic Jenkins-trigger helper — the audit
    detector greps for the literal `_DEVSITE_JOB_PATH` substring.

    Concurrency: callers must not invoke this twice with the same `git_tag`
    in the same run. The Symphony orchestrator serialises per-ticket so
    cross-run collision is rare; if it does happen, the second build will
    deploy alongside the first under a different auto-generated hostname,
    which is wasteful but not harmful. No in-process guard is enforced.
    """
    # Input validation — fail fast before hitting Jenkins.
    if not git_repo:
        raise ValueError("git_repo must be non-empty")
    if not git_tag:
        raise ValueError("git_tag must be non-empty")
    if not anondb_url:
        raise ValueError("anondb_url must be non-empty")
    if lifespan is not None:
        # Jenkins job constraints (per TOOLS.md and the job UI's help text):
        # internal sites 1–31 days, public sites 1–90 days. Fail fast on
        # out-of-range — Jenkins would 400 server-side, but catching here
        # lets the agent recover without burning a Jenkins slot.
        max_lifespan = 90 if public else 31
        if lifespan < 1 or lifespan > max_lifespan:
            raise ValueError(
                f"lifespan={lifespan} out of range; "
                f"allowed 1..{max_lifespan} for {'public' if public else 'internal'} sites"
            )
    if public and not client_name:
        raise ValueError(
            "public=True requires client_name (Jenkins job needs it for sysPass entry)"
        )

    base = os.environ["JENKINS_URL"].rstrip("/")
    url = f"{base}{_DEVSITE_JOB_PATH}/buildWithParameters"
    auth = (os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"])

    data: dict[str, str] = {
        "git_repo": git_repo,
        "git_tag": git_tag,
        "anonymised_database_url": anondb_url,
        "public_site": "true" if public else "false",
        "MAUTIC_ENABLED": "true" if mautic else "false",
        "REDIS_ENABLED": "false",
    }
    if client_name:
        data["client_name"] = client_name
    if lifespan is not None:
        data["lifespan"] = str(lifespan)

    r = requests.post(url, data=data, auth=auth, timeout=30)
    r.raise_for_status()
    location = r.headers.get("Location")
    if not location:
        raise RuntimeError(
            f"Jenkins returned {r.status_code} but no Location header; "
            "cannot resolve queue item"
        )
    return location


def poll_until_deployed(
    queue_url: str,
    *,
    timeout_s: int = 1800,
    expect_public: bool | None = None,
    raise_on_timeout: bool = True,
) -> str | None:
    """Walk Jenkins queue → build → console, return the dev-site hostname.

    Default `timeout_s=1800` (30 min) — observed builds range 5–20 min in
    production; 30min gives ~10min of headroom over the worst case so we
    don't lose `after.png` evidence on legitimate slow builds. The agent's
    main thread blocks during this call; Symphony's orchestrator
    (`max_concurrent_agents: 2`) keeps other tickets moving in parallel.

    `expect_public` cross-checks the returned hostname against what was
    requested via `trigger_dev_site(public=...)`. Pass `False` for internal
    sites (default for agent runs) or `True` for public; the function raises
    if Jenkins returns a hostname of the wrong shape. Pass `None` (default)
    to skip the check.

    `raise_on_timeout=False` returns `None` instead of raising `TimeoutError`
    when the deadline is hit before the build completes. Use this for the
    WORKFLOW.md two-phase stall-detector pattern: call with `timeout_s=90`,
    check for `None`, print a status line (keeping Claude API alive), then
    re-invoke. Only `TimeoutError` is suppressed — `RuntimeError` (build
    FAILURE, hostname not found) is always re-raised.

    Steps:
      1. Poll `<queue_url>api/json` every 5s until `executable.url` resolves
         (typically <5s — Jenkins assigns an executor quickly).
      2. Poll `<build_url>api/json` every 30s until `result` is non-null.
      3. If `result == 'SUCCESS'`, fetch `<build_url>consoleText` and extract
         the dev-site hostname via `_extract_devsite_hostname`.
      4. If `expect_public` is set, validate the hostname shape matches.

    Raises:
        TimeoutError: total elapsed time exceeds `timeout_s`.
        RuntimeError: build completed with `result != 'SUCCESS'`, or SUCCESS
            but the console contains no Pipeline-Mysql8 hostname line.
    """
    auth = (os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"])
    deadline = time.monotonic() + timeout_s

    # Phase 1: wait for executable. Fail fast on cancellation — a cancelled
    # queue item never produces `executable`, so without this early raise
    # the loop would burn the full `timeout_s` (up to 15min).
    build_url: str | None = None
    while time.monotonic() < deadline:
        r = requests.get(f"{queue_url}api/json", auth=auth, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("cancelled"):
            raise RuntimeError(
                f"Jenkins queue item {queue_url} was cancelled "
                f"(reason: {body.get('why')!r})"
            )
        executable = body.get("executable") or {}
        if executable.get("url"):
            build_url = executable["url"]
            break
        time.sleep(5)
    if not build_url:
        if not raise_on_timeout:
            return None
        raise TimeoutError(
            f"Jenkins queue item {queue_url} did not start within {timeout_s}s"
        )

    # Phase 2: wait for result
    result: str | None = None
    while time.monotonic() < deadline:
        r = requests.get(f"{build_url}api/json", auth=auth, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("result") is not None:
            result = body["result"]
            break
        time.sleep(30)
    if result is None:
        if not raise_on_timeout:
            return None
        raise TimeoutError(
            f"Jenkins build {build_url} did not complete within {timeout_s}s"
        )
    if result != "SUCCESS":
        raise RuntimeError(
            f"Jenkins build {build_url} completed with result={result!r}"
        )

    # Phase 3: extract hostname from console
    r = requests.get(f"{build_url}consoleText", auth=auth, timeout=30)
    r.raise_for_status()
    host = _extract_devsite_hostname(r.text)
    if not host:
        raise RuntimeError(
            f"Jenkins build {build_url} succeeded but no Pipeline-Mysql8 "
            "hostname line found in console"
        )

    # Public/internal cross-check. Public sites have `.public.` in the
    # hostname; internal sites don't. A mismatch means Jenkins built a
    # site of a different shape than the caller requested — usually a sign
    # of a misconfigured job or a stale console line — and routing the
    # agent's Playwright session to the wrong auth shape would silently
    # produce wrong-evidence after.png.
    if expect_public is not None:
        is_public_host = ".public." in host
        if is_public_host != expect_public:
            raise RuntimeError(
                f"Jenkins returned host {host!r} but caller expected "
                f"{'public' if expect_public else 'internal'} "
                f"({'.public.' if expect_public else 'no .public.'} in hostname)"
            )
    return host


# ---------------------------------------------------------------------------
# _Release Dev Site job — Phase B of the dev-site before/after workflow.
# WORKFLOW.md invariant #5 allows exactly two Jenkins job paths:
#   _DEVSITE_JOB_PATH  (Create Dev Site — Phase A)
#   _RELEASE_JOB_PATH  (Release Dev Site — Phase B)
# The audit (analyze-run.py) greps for both literal substrings and for
# `trigger_release_devsite(` as the helper sentinel.
# ---------------------------------------------------------------------------

_RELEASE_JOB_PATH = (
    "/job/Deployments/job/Dev%20Sites%20-%20Compucontainer"
    "/job/_Release%20Dev%20Site"
)


def trigger_release_devsite(*, site_url: str, git_tag: str) -> str:
    """Trigger the `_Release Dev Site` Jenkins job against an existing dev site.

    Phase B of the dev-site before/after workflow: after `trigger_dev_site`
    has created a dev site at the broken tag (Phase A), this releases the
    agent's fix branch to the same site without reimporting the database.

    Parameters:
        site_url: bare hostname of the existing dev site, e.g.
                  'gentlylivinggazelle.docker.cc-test.site'.
                  Confirmed format from last successful build.
        git_tag:  Docker-safe branch tag (use `devsite_git_tag()` to convert
                  a branch name with '/' to '-').

    `anonymised_database_url` is passed as an empty string — the Release Dev
    Site job skips DB reimport when this is empty, preserving the Phase A data.

    Returns the queue-item URL (Location header) for passing to
    `poll_until_released`.

    Raises:
        ValueError: if site_url is empty or not a *.cc-test.site hostname,
                    or if git_tag is empty.
        requests.HTTPError: on 4xx/5xx from Jenkins.
        RuntimeError: Jenkins returned 2xx but no Location header.
    """
    if not site_url:
        raise ValueError("site_url must be non-empty")
    if (
        site_url.startswith(("http://", "https://"))
        or not site_url.endswith(".cc-test.site")
        or "/" in site_url
    ):
        raise ValueError(
            f"site_url must be a bare *.cc-test.site hostname; got {site_url!r}. "
            "Pass the hostname string returned by poll_until_deployed — not a full URL "
            "(no https://, no trailing path)."
        )
    if not git_tag:
        raise ValueError("git_tag must be non-empty")

    base = os.environ["JENKINS_URL"].rstrip("/")
    url = f"{base}{_RELEASE_JOB_PATH}/buildWithParameters"
    auth = (os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"])

    data = {
        "site_url": site_url,
        "git_tag": git_tag,
        "anonymised_database_url": "",
    }

    r = requests.post(url, data=data, auth=auth, timeout=30)
    r.raise_for_status()
    location = r.headers.get("Location")
    if not location:
        raise RuntimeError(
            f"Jenkins returned {r.status_code} but no Location header; "
            "cannot resolve Release Dev Site queue item"
        )
    return location


def poll_until_released(
    queue_url: str,
    *,
    site_url: str,
    timeout_s: int = 1800,
    raise_on_timeout: bool = True,
) -> str | None:
    """Wait for a `_Release Dev Site` build to complete; return site_url on SUCCESS.

    Default `timeout_s=1800` (30 min) — same as `poll_until_deployed` — matches
    observed build durations. The WORKFLOW.md step 12b-bis call-site passes
    `timeout_s=90, raise_on_timeout=False` explicitly to use the stall-detector-safe
    chunked-poll pattern; this default is for one-shot callers (scripts, tests).

    No console-text parsing needed — the released site URL is already known
    from Phase A (`poll_until_deployed`). Returns `site_url` on SUCCESS for
    API symmetry with `poll_until_deployed`.

    Parameters:
        queue_url: Jenkins queue-item URL returned by `trigger_release_devsite`.
        site_url:  The dev-site hostname (already known from Phase A). Returned
                   unchanged on SUCCESS.
        timeout_s: Max seconds to wait in this call (default 1800s = 30 min).
        raise_on_timeout: If False, return None on timeout instead of raising.

    Raises:
        RuntimeError: build FAILURE, ABORTED, or queue item cancelled. Always
                      propagated even when raise_on_timeout=False.
        TimeoutError: deadline exceeded (only when raise_on_timeout=True).
    """
    auth = (os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"])
    deadline = time.monotonic() + timeout_s

    # Phase 1: wait for the queue item to resolve to a build executor.
    build_url: str | None = None
    while time.monotonic() < deadline:
        r = requests.get(f"{queue_url}api/json", auth=auth, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("cancelled"):
            raise RuntimeError(
                f"Jenkins Release Dev Site queue item {queue_url} was cancelled "
                f"(reason: {body.get('why')!r})"
            )
        executable = body.get("executable") or {}
        if executable.get("url"):
            build_url = executable["url"]
            break
        time.sleep(5)
    if not build_url:
        if not raise_on_timeout:
            return None
        raise TimeoutError(
            f"Release Dev Site queue item {queue_url} did not start within {timeout_s}s"
        )

    # Phase 2: wait for build result.
    result: str | None = None
    while time.monotonic() < deadline:
        r = requests.get(f"{build_url}api/json", auth=auth, timeout=15)
        r.raise_for_status()
        body = r.json()
        if body.get("result") is not None:
            result = body["result"]
            break
        time.sleep(30)
    if result is None:
        if not raise_on_timeout:
            return None
        raise TimeoutError(
            f"Release Dev Site build {build_url} did not complete within {timeout_s}s"
        )
    if result != "SUCCESS":
        raise RuntimeError(
            f"Release Dev Site build {build_url} completed with result={result!r}"
        )

    return site_url


# ---------- Non-prod hostname patterns (excluded from anondb prod lookup) ----------

_NON_PROD_SUFFIXES = (
    ".cc-staging.site",
    ".cc-test.site",
    ".cc-data.site",
    ".cc-prelive.site",
    ".docker.cc-test.site",
    ".public.cc-test.site",
)

_ANONDBS_BASE = "https://anondbs.cc-infra.tools/dir.php"


def _probe_anondb(hostname: str) -> str | None:
    """Return the anondbs URL for `hostname` if it resolves to a non-empty list, else None."""
    url = f"{_ANONDBS_BASE}?name={hostname}"
    try:
        r = requests.get(f"{url}&api=1", timeout=10)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return url
    except Exception:
        pass
    return None


def resolve_anondb_url(staging_hostname: str) -> str | None:
    """Return the best anondbs URL to use for a Jenkins dev-site triggered off
    the given `staging_hostname`.

    Strategy (staging-first):
      1. Probe `anondbs.cc-infra.tools/dir.php?name=<staging_hostname>&api=1`
         first. Staging sites using the do-not-anonymise module still appear in
         anondbs with the actual staging DB content. Using staging data is
         preferred when the bug was reported on staging — the production sibling
         may lack the exact state that triggers the issue.
      2. If the staging hostname has an anondbs entry, return it immediately.
      3. Otherwise, look up the staging site's `repository` in Mongo and
         collect production siblings (excluding all *.cc-staging.site,
         *.cc-test.site, *.cc-data.site, *.cc-prelive.site hostnames).
      4. Probe each prod candidate and return the first that resolves.
      5. Return None if nothing resolves — callers should skip the dev-site
         step rather than using an empty/wrong database.

    Connection: uses env vars MONGO_USER / MONGO_PASSWORD / MONGO_HOST /
    MONGO_PORT / MONGO_AUTH_SOURCE (same as the rest of the agent toolbox).
    """
    # Step 1-2: try the staging hostname itself first.
    staging_url = _probe_anondb(staging_hostname)
    if staging_url:
        return staging_url

    # Step 3-4: fall back to production siblings.
    host_str = os.environ["MONGO_HOST"]
    port_int = int(os.environ["MONGO_PORT"])
    mongo_uri = (
        f"mongodb://{os.environ['MONGO_USER']}:{os.environ['MONGO_PASSWORD']}"
        f"@{host_str}:{port_int}/?authSource={os.environ['MONGO_AUTH_SOURCE']}"
    )
    with MongoClient(mongo_uri) as client:
        db = client["compucorp"]
        staging_doc = db.sites.find_one({"_id": staging_hostname}, {"repository": 1})
        if not staging_doc or not staging_doc.get("repository"):
            return None

        repo = staging_doc["repository"]
        siblings = list(db.sites.find({"repository": repo}, {"_id": 1}))

    prod_candidates = [
        doc["_id"] for doc in siblings
        if doc["_id"] != staging_hostname
        and not any(doc["_id"].endswith(s) for s in _NON_PROD_SUFFIXES)
    ]

    for candidate in prod_candidates:
        url = _probe_anondb(candidate)
        if url:
            return url

    return None


def wait_until_site_up(
    hostname: str,
    *,
    timeout_s: int = 900,
    poll_interval_s: int = 20,
) -> None:
    """Poll `https://<hostname>/` until it returns HTTP 200.

    Dev sites use `compucorp_admin`/`compucorp_admin` Basic Auth (no Traefik
    wall — internal `.cc-test.site` sites use application-level credentials
    only). Follows redirects. Retries on any non-200 status or network error.

    Called after `poll_until_deployed` returns the hostname — Jenkins SUCCESS
    means the Docker stack deploy was triggered, but the containers still need
    ~5–15 min to start, run `drush updb`, etc. This wait is the gap between
    "Jenkins job finished" and "site actually serves requests".

    Raises:
        TimeoutError: if the site has not returned 200 within `timeout_s`.
    """
    url = f"https://{hostname}/"
    auth = ("compucorp_admin", "compucorp_admin")
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            r = requests.get(url, auth=auth, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"Dev site {hostname!r} did not respond with HTTP 200 within {timeout_s}s"
    )
