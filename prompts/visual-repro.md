# Visual Bug Reproduction — Operational Procedure

Read this when WORKFLOW.md step 10 invokes the visual-repro procedure. The goal is to reproduce a UI bug in a real browser against the affected staging site **before** writing the fix, to confirm root-cause understanding and produce a `before.png` for the PR.

## 1. When to apply (three-condition gate — ALL required)

The procedure runs only when:

- **(a) UI file types touched:** diff includes at least one of `*.tpl`, `*.scss`, `*.css`, files under `themes/`, files under `*.theme/*`, or compiled CSS in `dist/`.
- **(b) Staging host identifiable:** a specific staging URL can be resolved from the ticket (description, comments, or step 3b's Mongo lookup). Tickets in extension/profile repos (`compucorp/ase`, `compucorp/compuclient`, `compucorp/invoicehelper`) often touch UI but don't bind to a single site — for those, the gate fails (b) and falls through to manual verification with a `## Comments` note explaining which sites are affected.
- **(c) Staging host passes `assert_staging_host`** (within the allowlist).

If any condition fails: write `## Manual verification required` in the PR body and document the gate decision in `## Comments` ("Visual repro skipped: <reason>").

Within (a)+(b)+(c), if the bug isn't reproducible via browser automation (race condition, real-user content, PII, etc.), document the decision in `## Manual verification required` and skip the rest.

For **CSS-only diffs** (no `*.js`, `*.php`, `*.module`, `*.install`, `*.inc`, or `template.php` files in the diff), §8 additionally applies: an `after.png` is captured by runtime-injecting the equivalent CSS — no deploy required.

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

- **CSS/template, real navigation (Pattern 1 or 2):** <10 lines combined in `reproduce()` + `assert_bug_reproduced()`. Navigate to URL, dismiss cookie banner, assert one DOM property.
- **CSS/template, synthetic DOM repro** (no live page exhibits the bug class — construct representative DOM via `page.evaluate("document.body.innerHTML = ...")` and assert against the deployed CSS): 40–80 lines. Document the admin-content crawl in PR `## Comments` so the reviewer can verify no live path exists.
- **Role-gated or multi-step (Pattern 3 or complex Pattern 2):** 40–60 lines.
- **After-state capture pass (§8, CSS-only diffs only):** +30–50 lines on top of the above. Second pass with `add_style_tag` + fresh page + `assert_bug_fixed` + `after.png` capture.
- If you find yourself writing >120 lines total, the bug probably can't be cleanly reproduced via browser automation. Fall through to `## Manual verification required` instead.

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

**Artifact lifecycle:** the branch raw URL works during PR review. After PR merge + branch deletion, the URL stops resolving but the artifacts permanently land in master via the merge (~1–2 MB per UI ticket; doubled when §8 captures `after.png`). Accepted trade-off for v1.6 — the GitHub user-attachments CDN that humans drag-drop into PR bodies requires `user_session` cookie auth and is not accessible to PATs or GitHub Apps (cli/cli#13256, community#29993), so an asymmetric "after.png in user-attachments" pattern would require manual upload per PR. Object storage (S3, Cloudflare R2) is the cleaner alternative for v2 if repo bloat becomes material.

## 8. After-state capture for CSS-only fixes

When the substantive diff is exclusively CSS/SCSS/template (no JS, PHP, or other behavior-bearing code), capture an `after.png` showing the fix in effect by runtime-injecting the equivalent CSS into a fresh staging page. Same staging URL, same DOM, same session — only the CSS in scope differs. No deploy needed; CSS is declarative and the browser repaints with the injected rule.

### CSS-only gate

Run after the before.png pass succeeds. The diff is CSS-only when, excluding `.agent-artifacts/`, every changed file matches `*.scss`, `*.css`, `*.tpl`, OR lives under `themes/`, `*.theme/`, `dist/`. Detection:

```bash
# Gate: every changed file (excluding agent artifacts) must end in an allowlisted extension.
# Path-based exemptions (themes/, .theme/, dist/) are intentionally NOT used — they would
# silently let template.php, *.module, dist/js/*.js, and *.info through.
# Keep this command in sync with code-reviewer.md invariant 5.
cd <workspace>/repo
DEFAULT=$(gh api repos/<owner>/<repo> --jq .default_branch)
BEHAVIOR=$(git diff --name-only --diff-filter=ACM "$DEFAULT..HEAD" \
  | grep -v '^.agent-artifacts/' \
  | grep -vE '\.(scss|css|tpl|map)$' \
  | head -1)
if [ -z "$BEHAVIOR" ]; then
  echo "CSS-only — proceed with §8."
else
  echo "Behavior file in diff: $BEHAVIOR — skip §8, use manual-verification fallback."
fi
```

`.map` is allowlisted because `npm run dev` regenerates source maps alongside `dist/css/style.css`. If your build produces other sibling files (e.g., `.css.gz`, `.css.br`) under the same diff, extend the allowlist conservatively. `.tpl.php` is NOT allowlisted — those files mix markup with executable PHP; treat as behavior.

**Empty `dist/css/style.css` diff edge case.** If the only changed file is a `.map` (e.g., source map rebuilt without SCSS source edits), the gate passes but `FIX_CSS` would be empty. Guard with `git diff <default-branch>..HEAD -- 'dist/css/style.css' | grep -q '^+[^+]'` before proceeding; if empty, fall through to manual verification.

### Code pattern — extend `repro.py`'s `main()` with a second pass

```python
# Module top — equivalent of your SCSS diff, sourced from your built dist/css/style.css.
FIX_CSS = """
.example-class-from-your-diff {
  color: #FFFFFF;
}
"""


def assert_bug_fixed(page):
    """Inverse of assert_bug_reproduced. Symptom must be gone after CSS injection."""
    # e.g.:
    # color = page.locator("...").evaluate("e => getComputedStyle(e).color")
    # bg    = page.locator("...").evaluate("e => getComputedStyle(e).backgroundColor")
    # assert color != bg, f"Expected fix to change link color; got equal {color!r}=={bg!r}"
    pass


def main():
    pathlib.Path("before.png").unlink(missing_ok=True)
    pathlib.Path("after.png").unlink(missing_ok=True)
    basic = get_syspass_cred(SITE, prefer_name="Basic HTTP Auth")
    headless = os.environ.get("HEADED") != "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = basic_auth_context(browser, syspass_account_id=basic["id"])

            # ----- before.png pass (unchanged) -----
            page = ctx.new_page()
            reproduce(page)
            assert_bug_reproduced(page)
            page.screenshot(path="before.png", full_page=True)

            # ----- after.png pass (NEW — CSS-only diffs only) -----
            # Fresh page on the SAME context (auth/cookies preserved; CSS injection is per-document).
            page2 = ctx.new_page()
            reproduce(page2)                       # NAVIGATE FIRST — see note below
            page2.add_style_tag(content=FIX_CSS)   # inject AFTER reproduce's last navigation
            page2.wait_for_timeout(100)            # ok for color/visibility changes; see note for layout
            assert_bug_fixed(page2)
            page2.screenshot(path="after.png", full_page=True)
        finally:
            browser.close()
```

**Order matters: inject AFTER `reproduce()`, not before.** `reproduce()` often navigates (`page.goto(...)`, `page.click(<link>)`) and each navigation drops `<style>` tags from the previous document. Calling `reproduce()` first lands the injection on the final document where it persists for `assert_bug_fixed` + screenshot.

**Wait timing.** `wait_for_timeout(100)` reliably settles **paint-only** changes (`color`, `background`, `border-color`, `opacity`, `visibility`, `text-decoration`). If your fix affects **layout** (`display`, `flex`/`grid`, `width`/`height`, `margin`/`padding`, `font-size`, `position`, intrinsic sizing, font-swap reflow), 100ms can be too short — replace with `page2.wait_for_load_state("networkidle")` or `page2.locator("<affected-selector>").wait_for(state="visible")` keyed off the element your fix targets.

**Source `FIX_CSS` from your compiled output.** After running `npm run dev` (or your theme's build command), the relevant added rules sit in `dist/css/style.css`. Extract them with:

```bash
git diff <default-branch>..HEAD -- 'dist/css/style.css' \
  | grep '^+[^+]' \
  | sed 's/^+//'
```

The `grep '^+[^+]'` skips the `+++ b/<file>` header line; `sed 's/^+//'` strips the leading `+` from each diff line (literal `+` characters break CSS selectors). Don't try to recompile SCSS at script runtime.

### `assert_bug_fixed(page)` — inverse assertion

Define the structural inverse of `assert_bug_reproduced`, **using the same `page.locator(...)` selector** so you assert against the same element pre- and post-injection. Rewriting the selector for the after-pass can silently assert against a different element and pass for the wrong reason. The pair-assertion ensures the after-state actually flips the relevant DOM property, not just the screenshot pixels:

- Before: `assert link_color == container_bg` → After: `assert link_color != container_bg`
- Before: `assert "show" in collapse.class_list` → After: `assert "show" not in collapse.class_list`

### Required structure (parallel to §3)

If `after.png` is captured:
- `assert_bug_fixed(page)` is **defined AND called immediately before** `page.screenshot(path="after.png", ...)`. BLOCKER if absent or called after.
- **Stale-output guard:** first line of `main()` also unlinks `after.png` (shown in fixture above).

### Commit and PR embedding

`after.png` ships in the same artifact commit as `before.png`. The `git add .agent-artifacts/<TICKET>/` block in §7 already picks it up if you `cp ../after.png .agent-artifacts/<TICKET>/after.png` first.

PR `## After` section:

```markdown
![After — <one-line description of the fix>](https://github.com/<owner>/<repo>/raw/agent/<TICKET>-fix/.agent-artifacts/<TICKET>/after.png)

Captured by injecting the compiled equivalent of the SCSS change via `page.add_style_tag()` on the same staging URL — the fix is not yet deployed; injection simulates the post-deploy CSS state. The inverse assertion (`assert_bug_fixed`) fired before screenshot.
```

`after.png` follows the same agent-branch raw URL lifecycle as `before.png` (works during PR review, breaks after branch deletion, persists in master via merge).

### When to skip — manual-verification fallback

When the diff includes any executable-behavior file (`*.js`, `*.php`, `*.module`, `*.install`, `*.inc`, `template.php`), the runtime-inject technique does not produce a valid simulation — handlers bind on page-load and can't be retrofitted reliably. PR `## After` reads:

```markdown
## After

_Manual verification required:_ this fix changes runtime behavior; the visual repro captures the pre-fix state only. After deploying the fix to staging, re-run [`.agent-artifacts/<TICKET>/repro.py`](https://github.com/<owner>/<repo>/blob/agent/<TICKET>-fix/.agent-artifacts/<TICKET>/repro.py); `assert_bug_reproduced` should now FAIL — that inversion is the proof-of-fix.
```

### Failure handling

If `assert_bug_fixed` raises or `after.png` is missing after the run, do NOT commit a partial `after.png`. Document the gap in PR `## Comments` ("After-state capture attempted but failed: <reason>. Manual post-deploy verification required.") and use the manual-verification block above for `## After`.
