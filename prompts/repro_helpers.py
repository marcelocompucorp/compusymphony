"""Stable, low-churn helpers for visual bug reproduction across Compucorp Drupal 7 sites.

See docs/superpowers/specs/2026-05-15-visual-repro-design.md for the full design.

SECURITY: callers MUST NOT log returned credential dicts; passwords are plain.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests  # type: ignore

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

    # Step 1: search
    search_body = {
        "jsonrpc": "2.0",
        "method": "account/search",
        "params": {
            "authToken": os.environ["SYSPASS_TOKEN_SEARCH"],
            "tokenPass": os.environ["SYSPASS_PASS_SEARCH"],
            "text": account_search,
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
    try_cognito_bypass: bool = True,
) -> None:
    """Detect login form shape and drive the flow.

    Empirical validation status (2026-05-15):
      - SSP two-step: validated against ies2.cc-staging.site
      - Cognito-bypass: NOT validated — raises NotImplementedError
      - Standard one-step: NOT validated — raises NotImplementedError

    Raises if no logout link appears after the attempt.
    """
    site_root = page.url.split("/user/")[0] if "/user/" in page.url else None

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
    page.goto(f"{site_root or ''}/user/login" if site_root else "/user/login",
              wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    dismiss_cookie_banner(page)

    # Step 3: detect form shape
    ssp_form = page.locator("form#ssp-core-user-login-or-register-form").first
    if ssp_form.count() > 0 and ssp_form.is_visible():
        _drive_ssp_two_step(page, ssp_form, username, password)
    else:
        raise NotImplementedError(
            "compucorp_drupal_login_autodetect: only the SSP two-step form is "
            "validated. Standard one-step (#edit-name + #edit-pass on same page) "
            "and Cognito-bypass (/user/local/login) paths are not yet empirically "
            "validated. Fall through to manual verification, then add a smoke "
            "test for the new shape and implement here."
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
