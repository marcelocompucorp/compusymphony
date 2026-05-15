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
    Source: hostname patterns *.cc-staging.site, *.cc-data.site,
    *.cc-prelive.site, *.cc-dev.site. No escape hatch."""

# --- Credentials (sysPass) ---

def get_syspass_cred(account_search: str, *, prefer_name: str | None = None) -> dict:
    """Returns {login, password, url, id, name}. JSON-RPC account/search →
    filter by prefer_name (e.g. 'Drupal' to disambiguate from 'Basic HTTP Auth').
    Raises on zero or ambiguous match.
    SECURITY: callers MUST NOT log the returned dict — password is plain."""

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
    """Detects: Cognito-bypass (/user/local/login), SSP two-step
    (ssp_core_user_login_or_register_form), standard one-step. By form-shape,
    not site-config. Raises if no logout link after attempt.
    Set try_cognito_bypass=False to skip the HEAD probe on known-non-Cognito sites."""

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

### 1. When to apply (gate)

Diff touches at least one of: `*.tpl`, `*.scss`, `*.css`, files under `themes/`, files under `*.theme/*`, compiled CSS in `dist/`. If the file-type gate matches but you decide the bug isn't reproducible via browser automation (race condition, real-user content, PII), document the decision and skip to `## Manual verification required`.

### 2. Three patterns — copy the simplest that fits the bug

- **Pattern 1 — Anonymous public page.** Cookie-banner dismissal + navigate + assert + screenshot. ~25 lines total. Use when the bug is visible without login (landing page, public form display).
- **Pattern 2 — Admin-authenticated single-session.** Login as admin + navigate + assert + screenshot. ~30 lines. Use when admin can see the bug (most CMS UI bugs).
- **Pattern 3 — Test-user (non-admin) multi-session.** `lifecycle_test_user` context + two browser contexts + login in each as the test user + reproduce + screenshot. ~50 lines. Use ONLY when admin behaviour differs from non-admin (e.g. session limits, role-gated UI) OR when the bug requires multiple concurrent sessions.

The doc shows full code for each pattern, with `<<<AGENT FILLS>>>` markers for the two ticket-specific functions: `reproduce(page)` and `assert_bug_reproduced(page)`.

### 3. Required structure (all patterns)

- First non-import statement: `assert_staging_host(SITE)` — production safety rail
- `assert_bug_reproduced(page)` is **defined AND called before `page.screenshot(path="before.png", ...)`** — the proof-of-understanding contract
- Cleanup is in a `finally:` block (Pattern 3) or context manager (`lifecycle_test_user`) — best-effort, but the unconditional rule

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
10. **Visual verification (UI-changing PRs).** If the diff touches `*.tpl`,
    `*.scss`, `*.css`, files under `themes/`, `*.theme/*`, or compiled CSS in
    `dist/`, invoke the visual reproduction procedure:

   10a. Read `prompts/visual-repro.md`.
   10b. Pick the simplest pattern (1/2/3) that fits the bug; copy the skeleton
        to `<workspace>/repro.py`.
   10c. Fill the two functions: `reproduce(page)` and `assert_bug_reproduced(page)`.
   10d. Run: `python3 repro.py`. Outputs `before.png` on success.
   10e. If exit 0 AND `before.png` exists: embed in PR `## Before` via raw
        GitHub URL after push.
        Else: write `## Manual verification required` in PR body with explicit
        reproduction steps.
   10f. Commit `repro.py` (and `before.png` if present) as a separate commit
        from the fix: `{{ issue.identifier }}: add visual reproduction evidence`.

   If the file-type gate is NOT matched (non-UI fix), skip 10a-f and proceed
   to step 11. Document the decision in the PR `## Comments` section.
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

## Implementation effort estimate

- `prompts/repro_helpers.py`: ~150 lines of Python (6 helpers including lifecycle context manager + small utilities)
- `prompts/visual-repro.md`: ~1 page (~150 lines)
- `WORKFLOW.md` step 10 replacement: ~25 lines
- `prompts/code-reviewer.md` extension: ~10 lines
- Unit tests for helpers (mock Playwright): ~100 lines
- Integration smoke test against IES2 staging: re-uses the existing `/tmp/symphony-repro-test/` script as basis

Total: roughly one engineering day for the framework. Per-ticket cost is ~5–10 minutes of agent wall time + the fill-in effort (typically <10 lines for simple CSS bugs).

## Open follow-ups (not blocking v1)

1. Orphan test-user sweeper script + cron (when run frequency justifies)
2. `analyze-run.py` extension to parse `repro.py` for invariant violations (when audit data justifies)
3. Mongo-based staging allowlist (when first client-domain staging site appears)
4. Cognito-specific helper additions (when the first Cognito client gets a UI ticket)
5. `before.png` size cap enforcement (when pathological captures appear)
6. After-screenshot capability (separate design, requires deploy write access)

## Empirical validation

The mechanism in this design was empirically validated on 2026-05-15 against `ies2.cc-staging.site` for IESBUILD-267 (session-limit screen CSS overlap). End-to-end flow worked: sysPass → Basic Auth → SSP two-step login → admin user creation → two concurrent contexts → session-limit reproduced → screenshot captured → cleanup. Scripts and screenshots preserved in `/tmp/symphony-repro-test/`.
