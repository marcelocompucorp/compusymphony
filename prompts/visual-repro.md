# Visual Bug Reproduction — Operational Procedure

Read this when WORKFLOW.md step 10 invokes the visual-repro procedure. The goal is to reproduce a bug (UI or backend-with-observable-symptom) in a real browser against the affected staging site **before** writing the fix, to confirm root-cause understanding and produce a `before.png` for the PR.

## 1. When to apply (two-condition gate — both required)

The procedure runs when:

- **(a) Staging host identifiable:** a specific staging URL can be resolved from the ticket (description, comments, or step 3b's Mongo lookup). Tickets in extension/profile repos (`compucorp/ase`, `compucorp/compuclient`, `compucorp/invoicehelper`) often don't bind to a single site — for those, the gate fails (a) and falls through to manual verification with a `## Comments` note explaining which sites are affected.
- **(b) Staging host passes `assert_staging_host`** (within the allowlist).

If either condition fails: write `## Manual verification required` in the PR body and document the gate decision in `## Comments` ("Visual repro skipped: <reason>").

Within (a)+(b), the agent attempts to reproduce the bug regardless of which files the fix touches. The symptom is the trigger, not the diff. If the bug genuinely has no browser-observable symptom (rare — pure internal log line, race condition, etc.), document the decision in `## Manual verification required` and skip the rest.

**Scope note (changed):** earlier versions of this gate required the diff to touch UI files (`*.scss`/`*.tpl`/`*.css`/etc.). That has been dropped — bugs are filed because someone observed something wrong, regardless of where the fix lives. Most backend bugs surface through a UI page (the dblog, a Civi admin view, a Mautic preview), and the same Playwright harness can verify them. The CSS-file gate now lives on §8 only (it remains a real constraint on the *injection* technique).

For **CSS-only diffs** (no `*.js`, `*.php`, `*.module`, `*.install`, `*.inc`, or `template.php` files in the diff), §8 additionally applies: an `after.png` is captured by runtime-injecting the equivalent CSS — no deploy required. The CSS-only check is local to §8; it does NOT gate this top-level §1.

## 2. Three patterns — copy the simplest that fits the bug

Pick the **simplest pattern** that reproduces this specific bug. Don't reach for Pattern 3 unless the bug genuinely requires it.

### Pattern 1 — Anonymous public page

Use when the bug is visible without login (landing page, public form display).

```python
"""<TICKET>: <one-line bug description>"""
import os, pathlib
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
import os, pathlib
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
import os, pathlib, secrets
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
- `assert_bug_reproduced(page)` is **defined AND called immediately before `page.screenshot(path="before.png", ...)`** — the proof-of-understanding contract. For interaction-driven async state (popup open, transition complete, carousel advance), use Playwright's retrying `expect(...)` assertions per §8's "Async state assertions" sub-section — **never** `wait_for_timeout(N) + is_visible()`.
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

**`before.png` role when 12b-bis runs:** the staging before.png captured here is the **fallback** source for the PR's `## Before` section. When 12b-bis Phase A fires and `assert_bug_reproduced` passes on the dev site, the dev-site `before.png` supersedes this one (same data, same infra as the after-pass — stronger comparison). If Phase A fails or `assert_bug_reproduced` doesn't fire on the dev site, this staging screenshot is used instead. Always capture it regardless.

**Post-success: commit screenshots into the client repo on the agent branch.** After `assert_bug_reproduced` fires and `before.png` is captured, copy the screenshots (only — not the script) into `<workspace>/repo/.agent-artifacts/<TICKET>/` and commit them as a SEPARATE commit (not mixed with the fix):

```bash
cd <workspace>/repo
mkdir -p .agent-artifacts/<TICKET>/
cp ../before.png .agent-artifacts/<TICKET>/before.png
git add .agent-artifacts/<TICKET>/
git commit -m "<TICKET>: add visual reproduction evidence"
```

Only the screenshots ship to the client repo. `repro.py` and `repro_helpers.py` stay in `<workspace>/` for operator audit (and persist in Claude Code's per-session JSONL transcript at `~/.claude/projects/`). They are operator-internal tooling — client-repo maintainers don't read Python QA scripts in a Drupal theme repo, and committing them was redundant with the workspace + transcript copies.

**PR `## Before` section (when reproduction succeeded):** use markdown image syntax with the agent-branch raw URL:

```markdown
![Before — <one-line bug summary>](https://github.com/<owner>/<repo>/raw/agent/<TICKET>-fix/.agent-artifacts/<TICKET>/before.png)

Reproduction captured against `<staging URL>` (use the `SITE` constant from `repro.py`, e.g. `https://ies2.cc-staging.site`) via a Playwright assertion that fired before the screenshot was taken.
```

**Failure → no artifact commit.** If the script raises, the assertion fails, or `assert_staging_host` refuses, do NOT commit `before.png` (even if one was captured pre-assertion — see §3's stale-output guard + ordering rule). The artifact commit is gated on `assert_bug_reproduced` passing.

**Artifact lifecycle:** the branch raw URL works during PR review. After PR merge + branch deletion, the URL stops resolving but the artifacts permanently land in master via the merge (~1–2 MB per UI ticket; doubled when §8 captures `after.png`). Accepted trade-off for v1.6 — the GitHub user-attachments CDN that humans drag-drop into PR bodies requires `user_session` cookie auth and is not accessible to PATs or GitHub Apps (cli/cli#13256, community#29993), so an asymmetric "after.png in user-attachments" pattern would require manual upload per PR. Object storage (S3, Cloudflare R2) is the cleaner alternative for v2 if repo bloat becomes material.

## 8. After-state capture for CSS-only fixes (legacy — superseded when §9 applies)

**Use this section ONLY when §9 (agent-spun dev site) did not run** — i.e. the repo is not in `SITE_DEPLOYABLE_REPOS`, the Jenkins trigger was skipped per its own gates (doc-only diff, ambiguous anondb, missing token), or 12b-bis is otherwise unavailable. When §9 runs, the deployed-code `after.png` it produces supersedes this inject-based path — running both would emit two competing screenshots for the same diff.

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
BEHAVIOR=$(git diff --name-only --diff-filter=ACM "$DEFAULT...HEAD" \
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

**Empty `dist/css/style.css` diff edge case.** If the only changed file is a `.map` (e.g., source map rebuilt without SCSS source edits), the gate passes but `FIX_CSS` would be empty. Guard with `git diff <default-branch>...HEAD -- 'dist/css/style.css' | grep -q '^+[^+]'` before proceeding; if empty, fall through to manual verification.

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
git diff <default-branch>...HEAD -- 'dist/css/style.css' \
  | grep '^+[^+]' \
  | sed 's/^+//'
```

The `grep '^+[^+]'` skips the `+++ b/<file>` header line; `sed 's/^+//'` strips the leading `+` from each diff line (literal `+` characters break CSS selectors). Don't try to recompile SCSS at script runtime.

### `assert_bug_fixed(page)` — inverse assertion

Define the structural inverse of `assert_bug_reproduced`, **using the same `page.locator(...)` selector** so you assert against the same element pre- and post-injection. Rewriting the selector for the after-pass can silently assert against a different element and pass for the wrong reason. The pair-assertion ensures the after-state actually flips the relevant DOM property, not just the screenshot pixels:

- Before: `assert link_color == container_bg` → After: `assert link_color != container_bg`
- Before: `assert "show" in collapse.class_list` → After: `assert "show" not in collapse.class_list`

#### Async state assertions — use Playwright's retrying `expect`, not `wait_for_timeout + is_visible`

**For interaction-driven async state changes** (click → element appears/disappears, CSS transition fade, popup close, modal dismiss, dropdown open, carousel slide advance, polling-indicator update): use Playwright's retrying `expect(...)` assertions. The retrying form polls until the condition holds or the timeout expires — it degrades to ~instant for static cases (zero happy-path cost) and waits up to `timeout` for transitions, async DOM updates, or auto-advance cycles.

**Anti-pattern (do not use):**
```python
page.click("...")
page.wait_for_timeout(600)            # too short for ~500–1000ms Bootstrap fade
assert not popup.is_visible(), "..."  # races the close transition
```

**Correct pattern:**
```python
import re
from playwright.sync_api import expect

page.click("...")
expect(popup).to_be_hidden(timeout=10000)               # waits up to 10s for the popup to close
expect(menu).to_be_visible(timeout=10000)               # waits up to 10s for the menu to open

# Class containment — use a regex, NOT a bare string.
# `to_have_class("show")` matches the ENTIRE class attribute, so for
# Bootstrap's `class="collapse show"` the bare string fails. Use a
# word-boundary regex to match "show" anywhere in the class list.
expect(collapse).to_have_class(re.compile(r"\bshow\b"), timeout=10000)
expect(collapse).not_to_have_class(re.compile(r"\bshow\b"), timeout=10000)

expect(carousel.locator(".active-item")).to_have_text("02", timeout=10000)  # 5s auto-advance + headroom
```

The 10 s default covers all common cases (Bootstrap fades ~500 ms, modal animations ~300 ms, 5 s auto-advance carousel + headroom). Override only for unusually slow animations (`timeout=15000` etc.). **Do NOT bump `wait_for_timeout` instead of switching the pattern — fixed sleeps remain the anti-pattern even when longer.**

**If the fix removes the element entirely** (rather than hiding it), use `expect(locator).to_have_count(0, timeout=N)` rather than `to_be_hidden`. `to_be_hidden` passes if the locator matches no element, so it would pass for "element removed" by coincidence and could hide a different failure mode; `to_have_count(0)` is specific to the intended change.

**This guidance applies symmetrically to `assert_bug_reproduced`** — use `expect(locator).to_be_visible(timeout=10000)` for the before-state's "popup IS open after click" assertion, same retry semantics. (Anti-pattern is the same shape: `wait_for_timeout(N) + is_visible()` is brittle whether the bug is present or fixed.)

**Carve-out — when `wait_for_timeout` is legitimate:** the `page.wait_for_timeout(100)` after `add_style_tag` (§8 code pattern above) is for **CSS paint settlement before the assertion runs**, not for an animation. It's not the anti-pattern. The retrying `expect` inside the assertion makes it tolerant anyway, so leave the 100 ms paint-settle sleep. Other legitimate uses: post-`add_init_script` injection settle, Jenkins poll loops, network-idle waits. The anti-pattern is specifically `wait_for_timeout(N)` followed by an **immediate** `is_visible()` / `class_list` / `not popup.is_visible()` check inside `assert_bug_reproduced` or `assert_bug_fixed`.

`expect(...)` retrying assertions are available in `playwright-python` since version 1.20 (Feb 2022) — all modern releases include them. If you hit `AttributeError: ... has no attribute 'to_be_hidden'`, your agent runner has an unusually old Playwright; fall back to the structural-inverse class-list / computed-style assertion (still preferred over `wait_for_timeout + is_visible()`).

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

_Manual verification required:_ this fix changes runtime behavior; the captured before-state shows the bug. After deploy, manually confirm the symptom shown in the before screenshot is no longer present — perform the same user action (described below) against the deployed fix and verify the bug is gone.

Steps to manually verify post-deploy:
1. <one-line user-facing action that reproduced the bug, e.g.: "Log in as a non-admin user, click the search icon in the header, then click anywhere outside the search panel.">
2. Confirm: <expected post-fix outcome, e.g.: "The search panel closes immediately, matching standard click-away behaviour.">

If either step still reproduces the before-state, the fix needs follow-up.
```

### Failure handling

If `assert_bug_fixed` raises or `after.png` is missing after the run, do NOT commit a partial `after.png`. Document the gap in PR `## Comments` ("After-state capture attempted but failed: <reason>. Manual post-deploy verification required.") and use the manual-verification block above for `## After`.

## 9. Dev-site verification — before.png (§9a) and after.png (§9b)

WORKFLOW.md step 12b-bis spins up a dev site **twice**: once at the broken tag (Phase A → `before.png`) and once after releasing the fix branch (Phase B → `after.png`). Both screenshots come from the same dev site with the same data — a true apples-to-apples comparison. §9a covers Phase A; §9b covers Phase B.

This section is called by:
- `12b-bis` Phase A step A5 → §9a (before.png on dev site at broken tag)
- `12b-bis` Phase B step B3 → §9b (after.png after release)

### Scope — not bound by diff file types

§9 applies to **any bug whose symptom is observable on the dev site**, regardless of which files the fix touched. The §8 gate ("diff is CSS-only") was about whether *injection* is safe; §9 is real deployment, no such constraint.

The reasoning: if a bug was filed, it has an observable symptom — that's how the reporter noticed it. The agent's `reproduce` flow already walks to that observation point for `before.png`. §9a replays that same flow against the dev site (now at the broken tag + staging data) to confirm the bug is present; §9b asserts it's gone after the fix release.

Below are **illustrative examples**, not authoritative recipes — paths and admin menus vary across CiviCRM distributions and Compucorp client themes. The agent must verify the actual path on the dev site (open the page, find the trigger) before scripting it. The patterns are starting points; treat them as such.

| Bug type | `reproduce_after_state` (example) | `assert_bug_fixed` (example) |
|---|---|---|
| Theme/CSS rendering | Navigate to affected page | DOM property (colour, layout, visibility) |
| Form behaviour / JS | Fill form, trigger interaction | Submit-button state, validation message, error absence |
| Backend data adjustment (`hook_civicrm_pre` etc.) | Create/edit a record, view it | Record field shows expected value |
| Payment webhook | Trigger payment flow OR `page.request.post(WEBHOOK_URL, ...)` | Resulting contribution status / API response |
| CiviCRM API output reshape | Navigate to SearchKit/API4 explorer page | Expected field present in response |
| Drush / cron / queue worker | Locate the matching admin page (e.g. CiviCRM scheduled-jobs admin) and trigger a manual run | Result page / `/admin/reports/dblog` entry shows expected outcome |
| Permission rule | Log in as user X, navigate to page Y | HTTP 403 (or page renders, depending on the bug) |

**Email-template / Mautic tickets**: not currently supported via §9 on agent-spun dev sites. `trigger_dev_site` defaults to `MAUTIC_ENABLED=false` because most tickets don't need Mautic and enabling it adds ~30s build time. If a future email-template ticket needs §9, pass `mautic=True` to `trigger_dev_site` (helper param exists) and verify via Mautic's preview UI. For Phase 1, route email-template tickets through the `## Manual verification required` fall-through.

**60-second probe before scripting.** Before writing `reproduce_after_state`, the agent should spend ≤60s identifying a UI page where the bug's symptom is observable. If no such page exists (pure-internal effect — e.g. a log line that doesn't reach `/admin/reports/dblog`, an asynchronous queue effect not visible in any admin view, a race condition unsuitable for browser automation), **skip §9 with a `## Comments` note**: "Dev-site verification skipped: <bug-class> has no observable post-deploy surface; relying on unit tests + manual verification." Continue to 12c. This bounds the cost on tickets that turn out to be unverifiable through the dev-site path.

For HTTP-only surfaces (webhooks with no UI page), use `page.request.post(url, json=..., data=...)` — same Playwright context, exercises the deployed code, asserts on response. Don't shell out to `requests` separately; staying in the Playwright session keeps the audit clean.

### Inputs (provided by 12b-bis)

- `DEVSITE_HOST` — the auto-generated hostname (e.g. `quietfoggypanda.cc-test.site`), returned by `poll_until_deployed`. Shared across both phases — Phase B reuses the same site. Agent-spun dev sites are **internal** (`public_site=False` per WORKFLOW.md step 12b-bis) — no Traefik Basic Auth wall, reachable directly.
- `DEVSITE_URL` — `https://<DEVSITE_HOST>`. The hostname allowlist in `assert_staging_host` already accepts `*.cc-test.site` — no helper change needed.

### Credentials on a fresh agent-spun dev site

Different from §8's existing-staging-site path. Fresh sites have:

- **No Traefik Basic Auth.** `public_site=False` means no gateway wall — skip the `basic_auth_context` call entirely; a plain `browser.new_context()` works.
- **Drupal admin credentials depend on which DB was used.** Use `get_devsite_drupal_admin_creds(ANONDB_URL_USED)` where `ANONDB_URL_USED` is the value passed to `trigger_dev_site` as `anonymised_database_url` in Phase A:
  - **Staging DB** (bare `*.cc-staging.site` hostname, e.g. `ies2.cc-staging.site`): the dev site replicates the staging database, so the Drupal admin password is the same as on staging. `get_devsite_drupal_admin_creds` looks this up from sysPass automatically. Falls back to `compucorp_admin/compucorp_admin` if sysPass has no matching account.
  - **Anonymised DB** (anondbs URL or empty string): fresh anonymised database always uses `compucorp_admin` / `compucorp_admin`.

**Form-shape autodetect.** `compucorp_drupal_login_autodetect` handles both:
- SSP two-step (client staging sites with SSP theme — e.g. `ies2.cc-staging.site`)
- Standard Drupal 7 one-step (fresh dev sites without SSP)
The helper logs which shape it detected; check the agent transcript if login behaves unexpectedly.

### §9a — Phase A: before.png on dev site at broken tag

Called by 12b-bis step A5. The dev site is running `BASE_COMMIT` (the broken tag) with the staging or anondbs database. Run the same reproduce flow as the staging before-pass, but against the dev site.

```python
from repro_helpers import (
    assert_staging_host, compucorp_drupal_login_autodetect,
    dismiss_cookie_banner, get_devsite_drupal_admin_creds,
    DEFAULT_VIEWPORT,
)
import pathlib

DEVSITE_URL = "https://<DEVSITE_HOST>"     # from poll_until_deployed (Phase A)
ANONDB_URL_USED = "<value passed as anonymised_database_url to trigger_dev_site>"
assert_staging_host(DEVSITE_URL)
pathlib.Path("before.png").unlink(missing_ok=True)   # stale-output guard

admin_user, admin_pass = get_devsite_drupal_admin_creds(ANONDB_URL_USED)

with sync_playwright() as p:
    browser = p.chromium.launch()
    # No basic_auth_context — internal dev site has no Traefik wall.
    ctx = browser.new_context(viewport=DEFAULT_VIEWPORT)
    page = ctx.new_page()
    page.goto(f"{DEVSITE_URL}/<bug-page-path>")
    dismiss_cookie_banner(page)                # always call — no-op if not present
    # Only log in if the bug's symptom requires authentication to observe.
    # If the bug is visible anonymously, omit this call.
    compucorp_drupal_login_autodetect(page, admin_user, admin_pass,
                                      site=DEVSITE_URL)
    # Same reproduce flow as the staging before-pass.
    reproduce(page)
    assert_bug_reproduced(page)                # must fire — confirms bug is present
    page.screenshot(path="before.png", full_page=True)
    browser.close()
```

**Reproduction gate.** If `assert_bug_reproduced` does **not** fire: **STOP.** Do NOT fall back to staging `before.png`. Do NOT continue to Phase B. Post a Jira comment via the Atlassian MCP explaining (a) the dev site URL tested, (b) the `reproduce()` steps attempted, (c) that `assert_bug_reproduced` did not fire. Proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. A fix that cannot be confirmed as reproduced on real infrastructure must not be shipped. (Matches WORKFLOW.md Phase A A5 gate.)

### §9b — Phase B: after.png after release

Called by 12b-bis step B3. The same dev site is now running the agent's fix branch (same data, no DB reimport). Re-run the reproduce flow and assert the bug is gone.

```python
from repro_helpers import (
    assert_staging_host, compucorp_drupal_login_autodetect,
    dismiss_cookie_banner, get_devsite_drupal_admin_creds,
    DEFAULT_VIEWPORT,
)
import pathlib

DEVSITE_URL = "https://<DEVSITE_HOST>"     # same host as Phase A
ANONDB_URL_USED = "<same value used in Phase A>"
assert_staging_host(DEVSITE_URL)
pathlib.Path("after.png").unlink(missing_ok=True)    # stale-output guard

admin_user, admin_pass = get_devsite_drupal_admin_creds(ANONDB_URL_USED)

with sync_playwright() as p:
    browser = p.chromium.launch()
    # No basic_auth_context — internal dev site has no Traefik wall.
    ctx = browser.new_context(viewport=DEFAULT_VIEWPORT)
    page = ctx.new_page()
    page.goto(f"{DEVSITE_URL}/<bug-page-path>")
    dismiss_cookie_banner(page)                # always call — no-op if not present
    # Navigate to the bug location (same flow as before-pass `reproduce`).
    reproduce_after_state(page)
    assert_bug_fixed(page)                     # inverse assertion — must fire
    page.screenshot(path="after.png", full_page=True)

    # --- Logged-in regression check ---
    # Verify the fixed page still works correctly for an authenticated user.
    # This catches regressions where the fix broke the page for logged-in users
    # while appearing correct for anonymous visitors.
    # Only omit this check when the fix is purely anonymous-visible AND the
    # page is fully gated (logged-in state has no additional observable surface).
    page.goto(f"{DEVSITE_URL}/user/logout", wait_until="networkidle")
    compucorp_drupal_login_autodetect(page, admin_user, admin_pass,
                                      site=DEVSITE_URL)
    page.goto(f"{DEVSITE_URL}/<bug-page-path>", wait_until="networkidle")
    dismiss_cookie_banner(page)
    assert_bug_fixed(page)                     # must also fire when logged in
    # Take a separate logged-in screenshot if the page looks meaningfully
    # different from the anonymous view — append to after.png or save separately.

    browser.close()
```

### `assert_bug_fixed` contract

Same as §8: a Playwright assertion that fires when the bug is GONE. For non-DOM checks (HTTP response shape, API result), assert against the `Response` object from `page.request.*`. The screenshot is captured AFTER the assertion fires; if the assertion doesn't fire, `after.png` is not produced and step 12b-bis treats this as `blocked-verify` (per WORKFLOW.md AGENT_DONE schema). For pure-HTTP cases where a screenshot doesn't add evidence, the assertion firing without exception is itself the proof — capture a screenshot of `/admin/reports/dblog` or another evidence page as the post-verification artifact.

### Failure modes (mapped to WORKFLOW.md step 12b-bis)

| §9 failure | 12b-bis disposition |
|---|---|
| §9a: `assert_bug_reproduced` doesn't fire | **STOP.** Post Jira comment (URL tested, steps attempted, assertion did not fire). Proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. Do NOT open PR. |
| §9b: `assert_bug_fixed` doesn't fire | **BLOCK** — attempt recovery once per WORKFLOW.md Phase B recovery paragraph (diagnose + commit fix + re-trigger + re-assert). If the second attempt also fails: proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. No PR. |
| Playwright timeout / unreachable (either phase) | **STOP.** Post Jira comment naming the host + timeout cause. Proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. Apply `agent:blocked` label. (Symptom: dev site exists but Playwright can't reach it — could be transient network, dev-site post-deploy delay, or DNS. Operator decides whether to retry by archiving the workspace.) |
| Drupal login fails (`get_devsite_drupal_admin_creds` fallback tried, still rejected) | **STOP.** Post Jira comment naming the credential lookup path attempted. Proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. Apply `agent:blocked` label. Operator must inspect / restore dev-site Drupal admin credentials. |
| Logged-in regression check fails (`assert_bug_fixed` fires anonymous but not logged-in) | **BLOCK** — proceed to WORKFLOW.md step 15 with prefix `blocked-verify`. The fix breaks the authenticated user flow; do not open PR. (Recovery cap from Phase B's recovery paragraph applies here too — at most one recovery attempt per ticket.) |
| `assert_staging_host` rejects the host | Defensive: should never happen (Jenkins only produces `*.cc-test.site`). If it does, the safest action is to **STOP** — proceed to WORKFLOW.md step 15 with prefix `blocked-verify` and apply `agent:blocked`. Operator investigates Jenkins console-parsing logic in `repro_helpers.py`. |

### §9c — Small-element screenshots (device_scale_factor=3)

When the bug manifests on an element rendered below ~40px in either dimension (icon, badge, narrow counter, thin border), a full-page screenshot at default 1× DPI cannot resolve sub-pixel artifacts. Use a 3× HiDPI context with a tight element clip.

**When to use this:** icon rendering glitch (e.g. IESBUILD-229: FAQ expand `+` icon shows a `\` diagonal artifact at ~12px rendered size), wrong icon glyph, missing icon state, small badge, thin border inconsistency. Any bug where the artifact is smaller than ~5px at 1× DPI.

**Pattern A — element_handle.screenshot() (preferred for isolated element):**

```python
from playwright.sync_api import sync_playwright
from repro_helpers import assert_staging_host, dismiss_cookie_banner, DEFAULT_VIEWPORT

STAGING_URL = "https://<staging-host>"
assert_staging_host(STAGING_URL)

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(
        viewport=DEFAULT_VIEWPORT,
        device_scale_factor=3,   # 3× physical pixels per CSS pixel
    )
    page = ctx.new_page()
    page.goto(f"{STAGING_URL}/<bug-page-path>")
    dismiss_cookie_banner(page)
    reproduce(page)   # navigate to the element state where the bug is visible

    el = page.locator("<css-selector-for-small-element>").first
    el.wait_for(state="visible")

    assert_bug_reproduced(page)   # must fire before screenshot

    # Output is (element_width × 3) × (element_height × 3) physical px
    el.screenshot(path="before.png")

    ctx.close()
    browser.close()
```

**Pattern B — tight page clip (when surrounding context is needed):**

```python
    box = el.bounding_box()    # CSS-pixel coordinates on the logical page
    PADDING = 10               # extra CSS px on each side
    page.screenshot(
        path="before.png",
        clip={
            "x": max(0, box["x"] - PADDING),
            "y": max(0, box["y"] - PADDING),
            "width": box["width"] + 2 * PADDING,
            "height": box["height"] + 2 * PADDING,
        },
    )
```

**Writing assert_bug_reproduced for rendering bugs:** use a computed-style assertion where possible:

```python
def assert_bug_reproduced(page):
    # Example: icon renders at the wrong Unicode glyph.
    # Convention: assert_bug_reproduced *passes* when the bug IS present
    # (and raises when the page looks correct). Read the assert below as:
    # "fail if the glyph IS correct (= no bug to reproduce)."
    content = page.locator(".field-name-field-icon .fa-plus").evaluate(
        "el => window.getComputedStyle(el, '::before').content"
    )
    # Correct Font Awesome `+` glyph is U+F067. Any other value = bug present.
    assert content != '"\\F067"', \
        f"Bug not reproduced: icon renders correct glyph ({content!r})"
```

If no computed-style or accessibility assertion is available (purely visual artifact with no DOM property difference), capture at 3× and note in PR `## Before`: _"Screenshot captured at device_scale_factor=3 to reveal sub-pixel artifact. No programmatic DOM assertion available — element screenshot is the reproduction evidence."_ This is acceptable for rendering glitches where the artifact has no measurable DOM signal.

**Reproduction gate integration:** If the standard 1× repro fails and the bug description references a small UI element, try §9c before triggering the STOP. If §9c's `assert_bug_reproduced` fires, continue normally. If §9c also fails, STOP per the reproduction gate.

## 10. Video recording for interactive-behavior bugs (§9b extension)

Screenshots capture a single frame. When the bug's evidence is a **sequence of interactions** — what happens after a click, an animation completing, a dropdown closing — a screen recording is more informative. This section extends §9b Phase B to optionally capture `after.mp4` alongside `after.png`.

### 10.1 Gate — when to record video

Record video during §9b when the symptom is an **interactive-behavior sequence**: evidence requires showing what happens after one or more user actions.

**Record video when:**
- Click-away dismissal (element closes when clicking outside — e.g. IESBUILD-247)
- Dropdown/popup open-and-close sequences
- Form validation triggered by user input (focus/blur/keyup)
- CSS transitions or animations (fade, slide, expand) where timing is the evidence
- Multi-step flows where a mid-sequence state is the diagnostic artifact

**Skip video when a screenshot is sufficient:**
- CSS/color rendering issues (§8 path — a frame captures everything)
- Static layout or visibility problems
- Form field value display
- Anything where a single frame fully shows the before/after difference

**If in doubt, skip video.** Screenshots are the contract; video is supplementary evidence. A well-captioned `after.png` + descriptive `assert_bug_fixed` comment is always acceptable. Record video only when the sequence itself is what needs to be seen.

**Phase A video:** skip by default. "Nothing happening" doesn't benefit from video; `before.png` is sufficient. Exception: if the broken state IS a misfiring animation, record Phase A too using the same pattern.

### 10.2 Playwright recording and save

Playwright records `.webm` video when `record_video_dir` is passed to `new_context()`. The context must be closed (not just the browser) to flush. Use `video.save_as()` to move to a named path — it waits for the video to be fully written:

```python
import shutil, subprocess, pathlib

VIDEOS_DIR = pathlib.Path("videos")
RECORD_VIDEO = True  # ← agent sets this per §10.1 gate

# --- Video recording variant of §9b Phase B main() ---

pathlib.Path("after.png").unlink(missing_ok=True)
pathlib.Path("after.gif").unlink(missing_ok=True)
pathlib.Path("before.gif").unlink(missing_ok=True)
# Clean up any leftover .webm from prior runs:
if VIDEOS_DIR.exists():
    for f in VIDEOS_DIR.glob("*.webm"):
        f.unlink()

admin_user, admin_pass = get_devsite_drupal_admin_creds(ANONDB_URL_USED)

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ── Phase B: first pass — interactive behavior (recorded if RECORD_VIDEO) ──
    ctx_kwargs = {"viewport": DEFAULT_VIEWPORT}
    if RECORD_VIDEO:
        VIDEOS_DIR.mkdir(exist_ok=True)
        ctx_kwargs["record_video_dir"] = str(VIDEOS_DIR)
        ctx_kwargs["record_video_size"] = DEFAULT_VIEWPORT  # must match viewport

    ctx = browser.new_context(**ctx_kwargs)
    page = ctx.new_page()

    # Cursor overlay — makes mouse position visible in video recordings.
    # Playwright's headless Chromium does not render the OS cursor in .webm output.
    # This overlay tracks synthetic mousemove events and renders a red dot at the
    # cursor position. add_init_script() runs before every navigation in the context.
    page.add_init_script("""
        (() => {
            const dot = document.createElement('div');
            dot.style.cssText = (
                'position:fixed;top:0;left:0;width:20px;height:20px;' +
                'border-radius:50%;background:rgba(220,50,50,0.75);' +
                'box-shadow:0 0 0 3px rgba(255,255,255,0.85);' +
                'z-index:2147483647;pointer-events:none;' +
                'transform:translate(-50%,-50%);'
            );
            const attach = () => { if (document.body) document.body.appendChild(dot); };
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', attach);
            } else {
                attach();
            }
            document.addEventListener('mousemove', e => {
                dot.style.left = e.clientX + 'px';
                dot.style.top = e.clientY + 'px';
            }, { passive: true });
        })();
    """)

    page.goto(f"{DEVSITE_URL}/<bug-page-path>")
    dismiss_cookie_banner(page)
    compucorp_drupal_login_autodetect(page, admin_user, admin_pass, site=DEVSITE_URL)
    reproduce_after_state(page)       # drive to the point of evidence
    assert_bug_fixed(page)            # inverse assertion — must fire
    page.screenshot(path="after.png", full_page=True)

    if RECORD_VIDEO:
        video = page.video             # capture reference BEFORE ctx.close()
        ctx.close()                    # flush video to disk
        video.save_as("after.webm")    # move to named path; blocks until written
    else:
        ctx.close()

    # ── Phase B: logged-in regression check (separate non-recording context) ──
    # Video is scoped to the first pass only — the login + re-nav flow adds noise.
    ctx2 = browser.new_context(viewport=DEFAULT_VIEWPORT)
    page2 = ctx2.new_page()
    page2.goto(f"{DEVSITE_URL}/user/logout", wait_until="networkidle")
    compucorp_drupal_login_autodetect(page2, admin_user, admin_pass, site=DEVSITE_URL)
    page2.goto(f"{DEVSITE_URL}/<bug-page-path>", wait_until="networkidle")
    dismiss_cookie_banner(page2)
    assert_bug_fixed(page2)           # must also fire when logged in
    ctx2.close()

    browser.close()  # always after all contexts are closed
```

**Scope note:** video covers only the first `reproduce_after_state → assert_bug_fixed → screenshot` block. The logged-in regression check runs in a separate, non-recording context. This keeps the video focused on the interaction evidence and avoids 30+ second recordings that include login flows.

**Cursor visibility:** Playwright's headless Chromium does not render the OS cursor in video recordings — synthetic mouse movements from `page.mouse.move()` and `page.click()` are invisible without the overlay. `add_init_script()` runs before every page navigation in the context, so the overlay persists across `page.goto()` calls within the same recording. The overlay is purely cosmetic — it has `pointer-events:none` and does not affect `page.locator()` selectors, assertions, or click routing. **Caveat:** the overlay is re-injected only on full document loads. In-page SPA route changes (no new document) will not re-attach the dot — not a concern for Drupal 7 page loads, but worth knowing if the script is ever ported to a SPA framework.

### 10.3 Convert .webm → .gif

GitHub embeds external GIFs inline (proxied via `camo.githubusercontent.com`) but blocks external `<video>` sources via CSP. GIF is therefore the correct output format for PR embedding from a server environment with only a PAT — no browser session required.

**Size limit:** GitHub's camo proxy does not render GIFs larger than ~5 MB — they silently fail to display in the PR body. Keep output under 3 MB to be safe. The parameters below (fps=5, scale=700, max_colors=128, bayer dithering) produce ~1–2 MB for a typical 7–10 second recording. If the recording is longer or the scene is high-motion, reduce fps to 4 or scale to 600 and re-check the output size before uploading.

```python
# Gate: only convert if ffmpeg is available
if shutil.which("ffmpeg") is None:
    print("ffmpeg not found — keeping after.webm, skipping gif conversion.")
else:
    # Two-pass palette GIF optimised for GitHub's ~5 MB camo proxy limit.
    # fps=5 + scale=700 + max_colors=128 + bayer dithering keeps output
    # under 2 MB for a typical 7-10 s recording while remaining legible.
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", "after.webm",
            "-vf",
            "fps=5,scale=700:-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
            "-loop", "0",
            "after.gif",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"ffmpeg gif conversion failed:\n{result.stderr.decode()}")
        # after.webm is still available as fallback
    else:
        size_mb = pathlib.Path("after.gif").stat().st_size / 1_048_576
        if size_mb > 3:
            print(f"WARNING: after.gif is {size_mb:.1f} MB — may not render in GitHub. "
                  "Consider reducing fps to 4 or scale to 600 and rerunning.")
        pathlib.Path("after.webm").unlink(missing_ok=True)  # clean up source
```

`-loop 0` produces an infinitely looping GIF. `-y` overwrites existing output.

### 10.4 Artifact policy (workspace-only for source files)

- `videos/` and `after.webm`/`after.mp4` land in `<workspace>/` only — NOT committed to any git repo
- `after.gif` is uploaded to S3 and embedded in the PR body (see §10.5)
- The scoped `git add .agent-artifacts/<TICKET>/` in §7 naturally excludes `videos/`, `*.webm`, `*.mp4`, `*.gif` since they are not inside `.agent-artifacts/`

### 10.5 S3 upload and PR embedding

Upload `after.gif` (and optionally `before.gif`) to the artifacts bucket. GIF is the correct format — GitHub proxies external GIFs inline via `camo.githubusercontent.com` and they render as autoplaying animations in PR descriptions. External `<video>` tags from S3 are blocked by GitHub's CSP; GIF is the server-compatible alternative.

```python
import subprocess, os

bucket = os.environ["SYMPHONY_ARTIFACTS_BUCKET"]   # compuco-agents-artifacts-916270379481-eu-west-2-an
region = os.environ["SYMPHONY_ARTIFACTS_REGION"]   # eu-west-2
key_id = os.environ["SYMPHONY_ARTIFACTS_KEY_ID"]
secret = os.environ["SYMPHONY_ARTIFACTS_SECRET"]

for fname, s3_key in [("before.gif", f"{TICKET}/before.gif"), ("after.gif", f"{TICKET}/after.gif")]:
    if not pathlib.Path(fname).exists():
        continue
    public_url = f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"
    result = subprocess.run(
        ["aws", "s3", "cp", fname, f"s3://{bucket}/{s3_key}",
         "--region", region, "--content-type", "image/gif"],
        env={**os.environ, "AWS_ACCESS_KEY_ID": key_id, "AWS_SECRET_ACCESS_KEY": secret},
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"S3 upload failed for {fname}: {result.stderr.decode()}")
    else:
        print(f"Uploaded: {public_url}")
```

PR `## Before` / `## After` sections:

```markdown
## Before

![Before — <one-line bug description>](https://compuco-agents-artifacts-916270379481-eu-west-2-an.s3.eu-west-2.amazonaws.com/<TICKET>/before.gif)

## After

![After — <one-line fix description>](https://compuco-agents-artifacts-916270379481-eu-west-2-an.s3.eu-west-2.amazonaws.com/<TICKET>/after.gif)
```

The S3 URLs are permanent (public-read bucket policy, no expiry). GitHub proxies them through `camo.githubusercontent.com` — they render inline without any CSP issues.
