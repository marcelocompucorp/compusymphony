# Visual Bug Reproduction — Design

**Status:** Approved for implementation
**Author:** Marcelo (with iterative reviewer-subagent passes)
**Date:** 2026-05-15

## Overview

Symphony's agent should reproduce a UI bug in a real browser against the affected staging site **before** writing the fix. The reproduction confirms the agent understood the bug and is patching the right place, and produces a `before.png` for the PR `## Before` section.

This design (Path C) is the trimmed version after iteratively cutting bloat under reviewer pressure. The core mechanism — Playwright + sysPass + Drupal admin + multi-context browsers — is unchanged from the empirical test that successfully reproduced IESBUILD-267 (session-limit screen CSS overlap).

## Scope

**In scope (v1):**
- Per-ticket Playwright script written by the agent, lives in workspace as `repro.py`
- Three reusable patterns (anonymous / admin / test-user-multi-session) for the agent to pick from
- A small, stable Python helper module
- An operational doc the agent reads at WORKFLOW step 10
- A binary outcome model: programmatic assertion fires → embed `before.png`; otherwise fall through to `## Manual verification required`
- Production safety rail: refuse to run against non-staging hosts (no escape hatch)

**Out of scope (v2 / deferred):**
- After-screenshot loop (requires Docker write or deploy hook)
- Orphan test-user sweeper (manual on demand for v1)
- `analyze-run.py` post-hoc audit extension (reviewer-subagent enforces invariants in runtime instead)
- YAML schema for an audit file (`repro-result.md` removed entirely)
- Multi-status taxonomy (partial / skipped) — replaced by binary
- Pattern library for assertions (agent reads the skeleton + 8 gotchas)
- Visible-selector diff heuristic in the gate
- Mongo-based staging allowlist (hostname patterns suffice; revisit when first client-domain staging appears)
- Concurrency guarantees beyond `max_concurrent_agents: 1` (documented but not exercised)

## Architecture

Three artefacts in the `compuco-symphony` repo:

1. **`prompts/repro_helpers.py`** — Python module. Six stable helpers (§ Helper module API).
2. **`prompts/visual-repro.md`** — Operational doc. Agent reads at WORKFLOW step 10. Contains: gate criteria, three patterns the agent copies, 8 empirical gotchas, fallback rules (§ Operational doc).
3. **Per-ticket `repro.py`** — Written by the agent in the workspace based on one of the three patterns. Committed to the agent branch as a separate commit from the fix. Re-runnable by humans for debug.

Integration point: WORKFLOW.md step 10 (§ WORKFLOW changes).

## Helper module API — `prompts/repro_helpers.py`

```python
"""Stable, low-churn helpers for visual bug reproduction across Compucorp Drupal 7 sites."""

# --- Production safety rail (NON-NEGOTIABLE) ---

def assert_staging_host(url: str) -> None:
    """Refuse to proceed if `url`'s host is not a known staging environment.

    Allowlist (hostname patterns — match canonical Compucorp staging/test/dev/data
    surfaces; sourced from prompts/TOOLS.md §Compucorp client dev sites):
      - *.cc-staging.site
      - *.cc-data.site
      - *.cc-prelive.site
      - *.cc-dev.site
      - *.cc-test.site            (e.g. <slug>.public.cc-test.site)
      - *.public.cc-test.site     (explicit sub-pattern for the .public. variant)

    Custom-domain stagings (e.g. staging.<client>.org) are NOT covered by v1 and
    will hard-refuse. When the first such site appears, add Mongo-based fallback
    (see Open follow-ups). NO env-var or argument escape hatch — this is the only
    hard rail."""

# --- Credentials (sysPass) ---

def get_syspass_cred(account_search: str, *, prefer_name: str | None = None) -> dict:
    """Returns {login, password, url, id, name}.

    Two-step JSON-RPC flow against sysPass at $SYSPASS_URL:
      1. account/search with text=<account_search>, auth via
         $SYSPASS_TOKEN_SEARCH + $SYSPASS_PASS_SEARCH (tokenPass).
         Returns a list of accounts matching the search term.
      2. If prefer_name is set, filter the list to entries whose `name` field
         contains prefer_name (case-insensitive). Common values: 'Drupal' to
         get admin login, 'Basic HTTP Auth' to get Traefik gateway creds.
         If zero matches after filter → raise ValueError.
         If multiple matches still → raise ValueError listing them.
      3. account/viewPass with id=<filtered_id>, auth via
         $SYSPASS_TOKEN_VIEWPASS + $SYSPASS_PASS_VIEWPASS.
         Returns the password.
      4. Merge step 1's metadata + step 3's password into a single dict.

    Env vars required (all four already in ~/.claude/settings.json, auto-forwarded
    by start-symphony.sh's generic env-load):
      SYSPASS_URL, SYSPASS_TOKEN_SEARCH, SYSPASS_PASS_SEARCH,
      SYSPASS_TOKEN_VIEWPASS, SYSPASS_PASS_VIEWPASS

    SECURITY: callers MUST NOT log the returned dict — password is plain.

    Pre-implementation TODO: validate the two-step search→viewPass path
    empirically. The IESBUILD-267 empirical test used hard-coded account IDs
    with viewPass-only; the search path is unvalidated. First helper smoke
    test must exercise the full two-step flow."""

# --- Browser setup ---

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_TIMEOUT_MS = 30000

def basic_auth_context(browser, syspass_account_id: int) -> "BrowserContext":
    """Playwright context with Traefik Basic Auth + pinned viewport +
    default timeout set. No tracing, no video — minimal."""

# --- Drupal login (auto-detect 3 form shapes) ---

def compucorp_drupal_login_autodetect(
    page,
    username: str,
    password: str,
    *,
    try_cognito_bypass: bool = True,
) -> None:
    """Detects login form shape and drives the flow.

    Three shapes recognised:
      - Cognito-bypass via /user/local/login (when site has drupal_sso module)
      - SSP two-step (form id ssp_core_user_login_or_register_form)
      - Standard Drupal one-step (#edit-name + #edit-pass on same page)

    Detection is by form-shape, not site-config — resilient to per-site overrides.
    Raises if no logout link appears after the attempt.

    Empirical validation status (as of design date 2026-05-15):
      - SSP two-step: ✅ validated end-to-end against ies2.cc-staging.site
      - Cognito-bypass: ⚠️ NOT validated — implementer must add smoke test against
        a known Cognito site before declaring helper ready
      - Standard one-step: ⚠️ NOT validated — same caveat

    Until those two paths are validated, raise NotImplementedError with a clear
    message pointing the agent to fall through to manual verification. The first
    occurrence of an unvalidated shape signals work needed on the helper.

    Set try_cognito_bypass=False to skip the /user/local/login HEAD probe on
    known-non-Cognito sites (saves one round-trip)."""

# --- UI utility ---

def dismiss_cookie_banner(page) -> bool:
    """Best-effort dismiss of standard Compucorp cookie consent banner.
    Returns True if dismissed, False if absent. Non-fatal either way."""

# --- Test user lifecycle (context manager) ---

@contextmanager
def lifecycle_test_user(admin_page, username: str, password: str, email: str):
    """Creates a non-admin test user on enter, deletes on exit.
    Cleanup runs even on exception (Python context-manager semantics).
    Yields the username for convenience.

    Internally uses:
      - create_test_user via /admin/people/create (Drupal 7 fields:
        edit-pass-pass1 / edit-pass-pass2)
      - cancel_test_user_by_uid via /user/<uid>/cancel with user_cancel_delete
        and force=True (Drupal's label intercepts pointer events on radios)
      - find_uid_by_username as fallback if create succeeded but uid was not
        captured (orphan-cleanup safety)"""
```

**Excluded by design** (kept in per-ticket script, not the helper):
- Cognito federated login flow (varies per client)
- Generic `click()` / `fill()` / `wait()` wrappers
- Anything ticket-specific (the navigation, the assertion)

## Operational doc — `prompts/visual-repro.md`

Structure (~1 page, six short sections):

### 1. When to apply (gate — three conditions, ALL required)

The procedure runs only when:

- **(a) UI file types touched:** diff includes at least one of `*.tpl`, `*.scss`, `*.css`, files under `themes/`, files under `*.theme/*`, or compiled CSS in `dist/`.
- **(b) Staging host identifiable:** a specific staging URL can be resolved from the ticket (description / comments / step 3b's Mongo lookup). Tickets in extension/profile repos (`compucorp/ase`, `compucorp/compuclient`, `compucorp/invoicehelper`) often touch UI but don't bind to a single site — for those, the gate fails (b) and falls through to manual verification with a `## Comments` note explaining which sites are affected.
- **(c) Staging host passes `assert_staging_host`** (within the allowlist).

If any of (a), (b), (c) fails → skip the procedure entirely, write `## Manual verification required` in the PR body, and document the gate decision in `## Comments` (one line: "Visual repro skipped: <reason>").

Within (a)+(b)+(c), if you decide the bug isn't reproducible via browser automation (race condition, real-user content, PII), document the decision in `## Manual verification required` and skip the rest of the procedure.

### 2. Three patterns — copy the simplest that fits the bug

- **Pattern 1 — Anonymous public page.** Cookie-banner dismissal + navigate + assert + screenshot. ~25 lines total. Use when the bug is visible without login (landing page, public form display).
- **Pattern 2 — Admin-authenticated single-session.** Login as admin + navigate + assert + screenshot. ~30 lines. Use when admin can see the bug (most CMS UI bugs).
- **Pattern 3 — Test-user (non-admin) multi-session.** `lifecycle_test_user` context + two browser contexts + login in each as the test user + reproduce + screenshot. ~50 lines. Use ONLY when admin behaviour differs from non-admin (e.g. session limits, role-gated UI) OR when the bug requires multiple concurrent sessions.

The doc shows full code for each pattern, with `<<<AGENT FILLS>>>` markers for the two ticket-specific functions: `reproduce(page)` and `assert_bug_reproduced(page)`.

### 3. Required structure (all patterns)

- **First function call** in the script (after imports + module-level constant assignments like `SITE = "..."`) is `assert_staging_host(SITE)` — production safety rail.
- `assert_bug_reproduced(page)` is **defined AND called immediately before `page.screenshot(path="before.png", ...)`** — the proof-of-understanding contract.
- **Stale-output guard:** at the start of `main()`, delete any pre-existing `before.png` (`pathlib.Path("before.png").unlink(missing_ok=True)`). Prevents embedding a stale image from a prior failed run.
- Cleanup is in a `finally:` block (Pattern 3) or context manager (`lifecycle_test_user`) — best-effort, but the unconditional rule.

### 3.1 File locations and PR embedding

- `repro.py` lives at `<workspace>/repro.py` (workspace root, NOT inside `./repo/`). Same for `before.png`.
- **Neither file is committed to the client repo.** The audit trail comes from: (i) Symphony's existing per-session JSONL transcript in `~/.claude/projects/...` which captures every tool call including `page.screenshot()`; (ii) the workspace persists on the Symphony host until cleanup.
- **PR `## Before` embedding strategy for v1:** since the file isn't in the PR diff, the PR body section reads:
  > Reproduction completed; programmatic assertion fired. Screenshot at `~/symphony_workspaces/<KEY>/before.png` on the Symphony host. Re-run via `python3 ~/symphony_workspaces/<KEY>/repro.py` for live verification.
- Embedding `before.png` directly in the PR body is **deferred to v2** (options: gist upload via `gh api`, GitHub asset upload, or commit to a Symphony-internal audit branch). This is a real degradation versus the ideal — accepted for v1 because the script-as-artifact + assertion-fired contract still provides the proof-of-understanding the user wanted.

### 4. The 8 empirical gotchas

Compact patterns with helper pointers (each ≤2 lines):

1. Duplicate forms (mobile+desktop nav) → filter by `is_visible()`. *Agent-side.*
2. AJAX forms (`<button class="ajax-processed">`) → click + `wait_for_selector`. *Agent-side.*
3. `<button type=submit>` not `<input type=submit>` → use `button[type=submit], input[type=submit]`. *Agent-side.*
4. Labels intercept radio clicks → `.check(force=True)`. *Agent-side.*
5. VBO dropdowns → prefer direct URL `/user/<uid>/cancel`. See `repro_helpers.lifecycle_test_user`.
6. Compucorp login is SSP / Cognito / standard → use `repro_helpers.compucorp_drupal_login_autodetect`.
7. Drupal 7 password fields: `#edit-pass-pass1` + `#edit-pass-pass2`. See `lifecycle_test_user`.
8. Basic Auth via Playwright `http_credentials` → use `repro_helpers.basic_auth_context`. Never URL-embed.

### 5. Typical per-ticket effort

- Pure CSS/template (Pattern 1 or 2): <10 lines combined in the two filled functions.
- Role-gated or multi-step (Pattern 3 or complex Pattern 2): 40–60 lines.
- If you find yourself writing >80 lines, the bug probably can't be cleanly reproduced — fall through to manual verification.

### 6. Fallback

If the script raises an unhandled exception OR `assert_bug_reproduced` fails OR `assert_staging_host` refuses, write `## Manual verification required` in the PR body with explicit reproduction steps (URL, preconditions, what to look for). Do NOT commit a `before.png` from a script that didn't pass `assert_bug_reproduced`.

## WORKFLOW.md changes

Replace existing step 10 with:

```
10. **Visual verification (UI-changing PRs).** Apply the three-condition gate
    from `prompts/visual-repro.md` § 1: (a) diff touches `*.tpl/*.scss/*.css/themes/*.theme/dist`,
    AND (b) a specific staging URL is resolvable from the ticket, AND (c) the
    URL passes `assert_staging_host`. If any condition fails, document the gate
    decision in PR `## Comments` (one line) and proceed to step 11 with
    `## Manual verification required` in the PR body.

    When all three conditions hold:

    10a. Read `prompts/visual-repro.md`.
    10b. Pick the simplest pattern (1/2/3) that fits the bug; copy the skeleton
         to `<workspace>/repro.py` (workspace root — NOT inside `./repo/`).
    10c. Fill `reproduce(page)` and `assert_bug_reproduced(page)`. Stale-output
         guard at start of `main()` (delete pre-existing `before.png`).
    10d. Run: `python3 <workspace>/repro.py`. Outputs `<workspace>/before.png`
         on success.
    10e. If exit 0 AND `before.png` exists: PR `## Before` reads
         > "Reproduction completed; programmatic assertion fired. Screenshot
         > at `~/symphony_workspaces/{{ issue.identifier }}/before.png` on the
         > Symphony host. Re-run via `python3 ~/symphony_workspaces/{{ issue.identifier }}/repro.py`."
         Else: PR body gets `## Manual verification required` with explicit
         reproduction steps (URL, preconditions, what to look for).
    10f. Neither `repro.py` nor `before.png` is committed to the client repo
         in v1 — audit trail lives in workspace + Symphony's JSONL transcript.
```

No changes to invariant 9 (mandatory reviewer), invariant 4 (PR template), step 11 (commit and push), step 3b (deployed-ref check), or DRY-RUN OVERRIDE.

### Reviewer-subagent extension (minimal)

A small addition to `prompts/code-reviewer.md` (≤10 lines) so the reviewer knows about the visual-repro contract. When the workspace contains `repro.py` AND `before.png`, the reviewer additionally checks:

1. First non-import statement is `assert_staging_host(SITE)` — BLOCKER if absent.
2. `assert_bug_reproduced(page)` is defined AND is called before any `page.screenshot(path="before.png", ...)` — BLOCKER if missing, undefined, or called after the screenshot.
3. Cleanup of any created test user happens via `lifecycle_test_user` context manager OR explicit `finally:` block — BLOCKER if neither.

The reviewer uses the existing JSON output schema (new findings with `file="repro.py"`); no schema change.

## Safety rails (MVP)

| Rail | Status | Mechanism |
|---|---|---|
| Refuse non-staging hosts | **HARD** | `assert_staging_host` — no escape hatch |
| Deterministic test-user naming | **KEEP** | `symphony-test-<random-hex6>` — enables manual sweep later |
| Test-user auto-cleanup | **KEEP** | `lifecycle_test_user` context manager — `__exit__` runs on normal exceptions, raised assertions, and clean process exits. Orphans still possible on SIGKILL/hard crashes; covered by the deferred sweeper (v2). |
| Screenshot only after assertion | **HARD** | Required by `visual-repro.md` § 3; reviewer enforces |
| `before.png` size cap | **DEFER** | If pathological full-page screenshots appear, add later |
| Orphan sweeper | **DEFER (v2)** | Manual cleanup via admin UI when needed. Revisit at ~20 runs/week |
| Concurrency safety beyond N=1 | **DEFER** | Symphony is `max_concurrent_agents: 1`; design is concurrency-safe by construction (random suffix, isolated workspaces) but not exercised |

## Failure modes — coverage

| Failure | Outcome |
|---|---|
| Site in production allowlist? No | `assert_staging_host` raises; script aborts; PR falls back to manual verification |
| sysPass API down | `get_syspass_cred` raises; script aborts; PR falls back to manual verification |
| Staging site down | Playwright timeout; script aborts; PR falls back to manual verification |
| Login flow incompatible (new site pattern) | `compucorp_drupal_login_autodetect` raises; first occurrence signals a new pattern to add to the helper |
| Bug doesn't reproduce | `assert_bug_reproduced` raises; no `before.png` committed; PR falls back to manual verification |
| Test user already exists (random suffix collision, extremely improbable) | `lifecycle_test_user` raises on enter; script aborts; orphan check via admin UI |
| Script crash mid-run (handled exception) | `lifecycle_test_user.__exit__` runs; test user removed |
| Script process killed (SIGKILL, OOM, power) | Orphan test user remains; covered by deferred sweeper (v2) or manual cleanup |
| Symphony crash mid-run | AGENT_DONE sentinel + workspace preflight (existing v1 mechanisms); orphan test user remains until manual sweep |
| Reviewer subagent rejects after 3 rounds | Existing WORKFLOW invariant 9 N=3 cap; AGENT_DONE `blocked-review`, no PR |

## Implementation prerequisites (must be done before/during helper coding)

1. **Add a `## sysPass` section to `prompts/TOOLS.md`** documenting the four env vars (`SYSPASS_URL`, `SYSPASS_TOKEN_SEARCH`, `SYSPASS_PASS_SEARCH`, `SYSPASS_TOKEN_VIEWPASS`, `SYSPASS_PASS_VIEWPASS`), the two-step JSON-RPC flow (search → viewPass), and the account naming convention (`name` field disambiguates "Drupal" vs "Basic HTTP Auth" for the same site).

2. **Empirical validation of the two unvalidated paths** before declaring the helper module ready:
   - sysPass `account/search` two-step flow (the IES test used viewPass-by-ID; the spec mandates search-first)
   - Cognito-bypass login path (need to run against a known Cognito client site — Marcelo to identify a candidate staging host)
   - Standard one-step Drupal login (probably feasible against any non-SSP / non-Cognito Compucorp site — likely rare in current estate)

3. **Empirical validation of random-suffix username** lifecycle. The IES test used the fixed name `symphony-test`; the spec mandates `symphony-test-<hex6>`. Re-run the integration smoke test with the random-suffix variant to confirm both create and cancel flows are unaffected.

4. **Integration with DRY-RUN OVERRIDE**: when `agent:dry-run` label is present, the dry-run summary file (`<workspace>/dry-run-summary.md`) gains a section listing the visual-repro outcome (one of: `committed-repro`, `gate-skipped`, `assertion-failed`, `host-not-allowlisted`). Specifically: the existing dry-run summary template (in `WORKFLOW.md` § DRY-RUN OVERRIDE) needs one extra bullet under (e) for repro evidence status.

5. **N=3 reviewer-reject budget interaction**: because the skeleton in `visual-repro.md` bakes the structure (assert_staging_host first call, assert_bug_reproduced-before-screenshot, finally-cleanup), an agent that copies a pattern verbatim cannot fail reviewer rules 1 or 3 from § Reviewer-subagent extension. Only rule 2 (assert position vs screenshot call) is a judgment call. This ensures repro-shape BLOCKERs don't burn the shared N=3 reject budget unnecessarily.

## Implementation effort estimate

- `prompts/repro_helpers.py`: ~150 lines of Python (6 helpers including lifecycle context manager + small utilities)
- `prompts/visual-repro.md`: ~1 page (~150 lines)
- `prompts/TOOLS.md` — new `## sysPass` section: ~30 lines
- `WORKFLOW.md` step 10 replacement: ~30 lines
- `prompts/code-reviewer.md` extension: ~10 lines
- DRY-RUN OVERRIDE summary template: 1-line addition for repro evidence status
- Unit tests for helpers (mock Playwright + mock sysPass HTTP responses): ~150 lines
- Integration smoke tests against IES2 staging:
  - Re-validate sysPass two-step (search → viewPass): ~30 lines
  - Re-run repro with random-suffix username: re-uses existing `/tmp/symphony-repro-test/full_repro.py` with the new helper
  - Add a smoke test for Cognito-bypass login on an identified Cognito site (TODO: pick site)

Total: roughly one engineering day for the framework. Per-ticket cost is ~5–10 minutes of agent wall time + the fill-in effort (typically <10 lines for simple CSS bugs).

## Open follow-ups (not blocking v1)

1. Orphan test-user sweeper script + cron (when run frequency justifies)
2. `analyze-run.py` extension to parse `repro.py` for invariant violations (when audit data justifies)
3. Mongo-based staging allowlist (when first client-domain staging site appears)
4. Cognito-specific helper additions (when the first Cognito client gets a UI ticket)
5. `before.png` size cap enforcement (when pathological captures appear)
6. After-screenshot capability (separate design, requires deploy write access)

## Empirical validation (what's actually been proven)

End-to-end flow validated on 2026-05-15 against `ies2.cc-staging.site` for IESBUILD-267 (session-limit screen CSS overlap), with these specific path choices:
- sysPass: viewPass-by-ID with hard-coded account IDs (6277 = Basic Auth, 6278 = Drupal admin). **`account/search` flow NOT validated.**
- Basic Auth: ✅ via Playwright `http_credentials`
- Login: ✅ SSP two-step (`ssp_core_user_login_or_register_form`). **Cognito-bypass and standard one-step NOT validated.**
- Admin user creation: ✅ via `/admin/people/create`, fields `edit-pass-pass1`/`edit-pass-pass2`
- Test username pattern: **fixed `symphony-test` (NOT the spec's random-suffix variant)**
- Two concurrent contexts: ✅ via `browser.new_context()`
- Session-limit reproduction: ✅ exact URL `/session/limit`, screenshot captured matching ticket attachment
- Cleanup: ✅ via `/user/<uid>/cancel` with `force=True` on the cancel-method radio

Scripts and outputs preserved in `/tmp/symphony-repro-test/`.

The validated subset is enough to prove the **mechanism**; the spec's expanded surface (search flow, random suffix, login autodetect for 3 shapes) needs the prerequisites in § Implementation prerequisites before the helper can be declared production-ready.
