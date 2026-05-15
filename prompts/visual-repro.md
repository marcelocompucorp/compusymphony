# Visual Bug Reproduction — Operational Procedure

Read this when WORKFLOW.md step 10 invokes the visual-repro procedure. The goal is to reproduce a UI bug in a real browser against the affected staging site **before** writing the fix, to confirm root-cause understanding and produce a `before.png` for the PR.

## 1. When to apply (three-condition gate — ALL required)

The procedure runs only when:

- **(a) UI file types touched:** diff includes at least one of `*.tpl`, `*.scss`, `*.css`, files under `themes/`, files under `*.theme/*`, or compiled CSS in `dist/`.
- **(b) Staging host identifiable:** a specific staging URL can be resolved from the ticket (description, comments, or step 3b's Mongo lookup). Tickets in extension/profile repos (`compucorp/ase`, `compucorp/compuclient`, `compucorp/invoicehelper`) often touch UI but don't bind to a single site — for those, the gate fails (b) and falls through to manual verification with a `## Comments` note explaining which sites are affected.
- **(c) Staging host passes `assert_staging_host`** (within the allowlist).

If any condition fails: write `## Manual verification required` in the PR body and document the gate decision in `## Comments` ("Visual repro skipped: <reason>").

Within (a)+(b)+(c), if the bug isn't reproducible via browser automation (race condition, real-user content, PII, etc.), document the decision in `## Manual verification required` and skip the rest.

## 2. Three patterns — copy the simplest that fits the bug

Pick the **simplest pattern** that reproduces this specific bug. Don't reach for Pattern 3 unless the bug genuinely requires it.

### Pattern 1 — Anonymous public page

Use when the bug is visible without login (landing page, public form display).

```python
"""<TICKET>: <one-line bug description>"""
import sys, os, pathlib
sys.path.insert(0, "/Users/mar/projects/compuco-symphony/prompts")
from playwright.sync_api import sync_playwright
from repro_helpers import (
    assert_staging_host, basic_auth_context, get_syspass_cred,
    dismiss_cookie_banner,
)

SITE = "https://<host>.cc-staging.site"
assert_staging_host(SITE)


def reproduce(page):
    """<<<AGENT FILLS: navigate / wait / set state>>>"""
    page.goto(SITE)
    dismiss_cookie_banner(page)
    # ... more navigation specific to the bug


def assert_bug_reproduced(page):
    """<<<AGENT FILLS: simplest check that fails iff the bug isn't there>>>"""
    pass


def main():
    pathlib.Path("before.png").unlink(missing_ok=True)
    basic = get_syspass_cred(SITE, prefer_name="Basic HTTP Auth")
    headless = os.environ.get("HEADED") != "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = basic_auth_context(browser, syspass_account_id=basic["id"])
            page = ctx.new_page()
            reproduce(page)
            assert_bug_reproduced(page)
            page.screenshot(path="before.png", full_page=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
```

### Pattern 2 — Admin-authenticated single-session

Use when the admin user can see the bug (most CMS UI bugs).

```python
"""<TICKET>: <one-line bug description>"""
import sys, os, pathlib
sys.path.insert(0, "/Users/mar/projects/compuco-symphony/prompts")
from playwright.sync_api import sync_playwright
from repro_helpers import (
    assert_staging_host, basic_auth_context, get_syspass_cred,
    compucorp_drupal_login_autodetect,
)

SITE = "https://<host>.cc-staging.site"
assert_staging_host(SITE)


def reproduce(page):
    """<<<AGENT FILLS>>>"""
    pass


def assert_bug_reproduced(page):
    """<<<AGENT FILLS>>>"""
    pass


def main():
    pathlib.Path("before.png").unlink(missing_ok=True)
    basic = get_syspass_cred(SITE, prefer_name="Basic HTTP Auth")
    admin = get_syspass_cred(SITE, prefer_name="Drupal")
    headless = os.environ.get("HEADED") != "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = basic_auth_context(browser, syspass_account_id=basic["id"])
            page = ctx.new_page()
            compucorp_drupal_login_autodetect(
                page, admin["login"], admin["password"],
                site=SITE, try_cognito_bypass=False)
            reproduce(page)
            assert_bug_reproduced(page)
            page.screenshot(path="before.png", full_page=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
```

### Pattern 3 — Test-user (non-admin) multi-session

Use ONLY when admin behaviour differs from non-admin (e.g. session limits, role-gated UI) OR when the bug requires multiple concurrent sessions.

```python
"""<TICKET>: <one-line bug description>"""
import sys, os, pathlib, secrets
sys.path.insert(0, "/Users/mar/projects/compuco-symphony/prompts")
from playwright.sync_api import sync_playwright
from repro_helpers import (
    assert_staging_host, basic_auth_context, get_syspass_cred,
    compucorp_drupal_login_autodetect, lifecycle_test_user,
)

SITE = "https://<host>.cc-staging.site"
assert_staging_host(SITE)


def reproduce(page1, page2):
    """<<<AGENT FILLS — drive whichever pages the bug needs>>>"""
    pass


def assert_bug_reproduced(page):
    """<<<AGENT FILLS — page argument is whichever one captures the bug>>>"""
    pass


def main():
    pathlib.Path("before.png").unlink(missing_ok=True)
    basic = get_syspass_cred(SITE, prefer_name="Basic HTTP Auth")
    admin = get_syspass_cred(SITE, prefer_name="Drupal")
    test_username = f"symphony-test-{secrets.token_hex(3)}"
    test_password = "Sym!" + secrets.token_urlsafe(12)
    headless = os.environ.get("HEADED") != "1"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            admin_ctx = basic_auth_context(browser, syspass_account_id=basic["id"])
            admin_page = admin_ctx.new_page()
            compucorp_drupal_login_autodetect(
                admin_page, admin["login"], admin["password"],
                site=SITE, try_cognito_bypass=False)

            with lifecycle_test_user(admin_page, test_username, test_password,
                                     f"{test_username}@compuco.invalid"):
                ctx1 = basic_auth_context(browser, syspass_account_id=basic["id"])
                p1 = ctx1.new_page()
                compucorp_drupal_login_autodetect(
                    p1, test_username, test_password,
                    site=SITE, try_cognito_bypass=False)

                ctx2 = basic_auth_context(browser, syspass_account_id=basic["id"])
                p2 = ctx2.new_page()
                compucorp_drupal_login_autodetect(
                    p2, test_username, test_password,
                    site=SITE, try_cognito_bypass=False)

                reproduce(p1, p2)
                assert_bug_reproduced(p2)  # or p1, depending on the bug
                p2.screenshot(path="before.png", full_page=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
```

## 3. Required structure (all patterns)

These three rules are validated by the code-reviewer subagent. Violations are BLOCKERs.

- **First function call** in the script (after imports + module-level constant assignments like `SITE = "..."`) is `assert_staging_host(SITE)` — production safety rail.
- `assert_bug_reproduced(page)` is **defined AND called immediately before `page.screenshot(path="before.png", ...)`** — the proof-of-understanding contract.
- **Stale-output guard:** first line of `main()` is `pathlib.Path("before.png").unlink(missing_ok=True)`. Prevents embedding a stale image from a prior failed run.

## 4. The 8 empirical gotchas (Compucorp Drupal 7)

These were discovered against `ies2.cc-staging.site` and may apply on other sites.

1. **Duplicate forms (mobile+desktop nav)** → filter by `is_visible()` before action. *Agent-side.*
2. **AJAX forms (`<button class="ajax-processed">`)** → click button + `wait_for_selector` for the next element. `expect_navigation` won't fire (DOM updates in place). *Agent-side.*
3. **`<button type=submit>` not `<input type=submit>`** → selector must include both: `button[type=submit], input[type=submit]`. *Agent-side.*
4. **Labels intercept radio clicks** → `.check(force=True)`. *Agent-side.*
5. **VBO operation dropdowns** at `/admin/people` use values like `action::views_bulk_operations_user_cancel_action`. Direct URL `/user/<uid>/cancel` is simpler. See `repro_helpers.cancel_test_user_by_uid`.
6. **Compucorp login is SSP two-step on most sites; Cognito-bypass on others; standard one-step on legacy.** Use `repro_helpers.compucorp_drupal_login_autodetect`. Only SSP is validated empirically; the other two raise `NotImplementedError`.
7. **Drupal 7 password fields**: `#edit-pass-pass1` + `#edit-pass-pass2` (not `#edit-pass`). See `repro_helpers.lifecycle_test_user`.
8. **Basic Auth via Playwright `http_credentials`** — clean, first-try. Use `repro_helpers.basic_auth_context`. Never URL-embed `user:pass@host`.

## 5. Typical per-ticket effort

- **Pure CSS/template (Pattern 1 or 2):** <10 lines combined in `reproduce()` + `assert_bug_reproduced()`. Navigate to URL, dismiss cookie banner, assert one DOM property.
- **Role-gated or multi-step (Pattern 3 or complex Pattern 2):** 40–60 lines.
- If you find yourself writing >80 lines of `reproduce()`, the bug probably can't be cleanly reproduced via browser automation. Fall through to `## Manual verification required` instead.

## 6. Fallback

If the script:
- Raises an unhandled exception, OR
- `assert_bug_reproduced` fails, OR
- `assert_staging_host` refuses (custom-domain staging not yet in allowlist)

→ Write `## Manual verification required` in the PR body with explicit reproduction steps (URL, browser-state preconditions, what to look for). **Do NOT commit a `before.png`** from a script that didn't pass `assert_bug_reproduced`. The audit trail remains via Symphony's per-session JSONL transcript.

## 7. File locations and PR embedding (v1.5)

**Generation locations (during script run):**
- `repro.py` is written at `<workspace>/repro.py` (workspace root, NOT inside `./repo/`).
- `before.png` is written by the script at `<workspace>/before.png`.

**Post-success: commit into the client repo on the agent branch.** After `assert_bug_reproduced` fires and `before.png` is captured, copy both files into `<workspace>/repo/.agent-artifacts/<TICKET>/` and commit them as a SEPARATE commit (not mixed with the fix):

```bash
cd <workspace>/repo
mkdir -p .agent-artifacts/<TICKET>/
cp ../repro.py .agent-artifacts/<TICKET>/repro.py
cp ../before.png .agent-artifacts/<TICKET>/before.png
git add .agent-artifacts/<TICKET>/
git commit -m "<TICKET>: add visual reproduction evidence"
```

**PR `## Before` section (when reproduction succeeded):** use markdown image syntax with the agent-branch raw URL:

```markdown
![Before — <one-line bug summary>](https://github.com/<owner>/<repo>/raw/agent/<TICKET>-fix/.agent-artifacts/<TICKET>/before.png)

Reproduction completed; programmatic assertion fired. Reproduction script at [`.agent-artifacts/<TICKET>/repro.py`](https://github.com/<owner>/<repo>/blob/agent/<TICKET>-fix/.agent-artifacts/<TICKET>/repro.py) — re-runnable from a fresh checkout via `python3 .agent-artifacts/<TICKET>/repro.py` (requires `SYSPASS_*` env + Playwright + chromium).
```

**Failure → no artifact commit.** If the script raises, the assertion fails, or `assert_staging_host` refuses, do NOT commit `before.png` (even if one was captured pre-assertion — see §3's stale-output guard + ordering rule). The artifact commit is gated on `assert_bug_reproduced` passing.

**Artifact lifecycle:** the branch raw URL works during PR review. After PR merge + branch deletion, the URL stops resolving but the artifacts permanently land in master via the merge (~1MB per UI ticket). Accepted trade-off for v1.5 — alternatives (gist, side branch, GitHub user-attachments) are higher-friction.
