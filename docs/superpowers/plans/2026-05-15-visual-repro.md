# Visual Bug Reproduction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the visual bug reproduction capability per the design spec at `docs/superpowers/specs/2026-05-15-visual-repro-design.md`. Symphony's agent will reproduce UI bugs in a real browser against staging before writing the fix.

**Architecture:** Python helper module + operational doc + WORKFLOW.md integration. Agent writes a per-ticket `repro.py` (workspace root, not committed to client repo) using one of three patterns from `prompts/visual-repro.md`, calls helpers from `prompts/repro_helpers.py`, runs the script to capture `before.png` and confirm root-cause understanding via a mandatory `assert_bug_reproduced` check.

**Tech Stack:** Python 3.13, Playwright (sync API), pytest, sysPass JSON-RPC API, the existing Elixir orchestrator (Symphony) unchanged.

---

## File structure

**New files:**
- `prompts/repro_helpers.py` — single Python module (~150 lines) with 6 helpers + lifecycle context manager
- `prompts/visual-repro.md` — operational doc the agent reads on-demand
- `tests/python/conftest.py` — pytest fixtures (mock Playwright, mock requests)
- `tests/python/test_repro_helpers.py` — unit tests
- `tests/python/integration/test_repro_smoke.py` — end-to-end smoke against IES2 staging (gated on env vars)
- `tests/python/pytest.ini` — minimal pytest config

**Modified files:**
- `prompts/TOOLS.md` — append `## sysPass` section
- `prompts/code-reviewer.md` — append visual-repro invariants (3 BLOCKER checks)
- `WORKFLOW.md` — replace step 10 + add one bullet to DRY-RUN OVERRIDE summary template

**Not modified:**
- `elixir/lib/symphony_elixir/*` — orchestrator unchanged
- `prompts/INVESTIGATION.md`, `prompts/PLAYBOOKS.md` — orthogonal
- `analyze-run.py` — extension deferred to v2 per spec

---

## Task 1: Set up Python test infrastructure

**Files:**
- Create: `tests/python/pytest.ini`
- Create: `tests/python/__init__.py` (empty)
- Create: `tests/python/integration/__init__.py` (empty)

- [ ] **Step 1: Create pytest.ini**

Create `tests/python/pytest.ini`:

```ini
[pytest]
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    -v
    --tb=short
    --strict-markers
markers =
    integration: requires live staging site + sysPass env vars (skipped by default)
```

- [ ] **Step 2: Create empty __init__.py files**

```bash
touch tests/python/__init__.py
touch tests/python/integration/__init__.py
```

- [ ] **Step 3: Verify pytest can discover tests**

Run:
```bash
cd /Users/mar/projects/compuco-symphony && python3 -m pytest tests/python -v --co
```

Expected: `no tests ran` (or empty collection, exit 5). Confirms discovery works.

- [ ] **Step 4: Commit**

```bash
git add tests/python/pytest.ini tests/python/__init__.py tests/python/integration/__init__.py
git commit -m "Add Python pytest scaffold for visual-repro helpers"
```

---

## Task 2: Add sysPass section to prompts/TOOLS.md

**Files:**
- Modify: `prompts/TOOLS.md` (append section)

- [ ] **Step 1: Append the new section**

Append to `prompts/TOOLS.md` after the existing sections (use `Read` first to find the right insertion point — after the last existing service section but before any footer / general notes):

```markdown
## sysPass (Compucorp self-hosted password manager)

**Purpose:** retrieve Drupal admin + Traefik Basic Auth credentials for staging sites, needed by the visual-repro skill (`prompts/visual-repro.md`).

**Endpoint:** `$SYSPASS_URL/api.php` — JSON-RPC 2.0.

**Auth:** sysPass uses per-action API tokens. The agent has two:
- `$SYSPASS_TOKEN_SEARCH` + `$SYSPASS_PASS_SEARCH` — authorized for `account/search`
- `$SYSPASS_TOKEN_VIEWPASS` + `$SYSPASS_PASS_VIEWPASS` — authorized for `account/viewPass`

All four env vars live in `~/.claude/settings.json` and are auto-forwarded by `start-symphony.sh`'s generic env-load (lines 67–83). Do NOT log them.

**Two-step credential lookup pattern:**

```python
# Step 1: account/search by site URL or name
search_response = {
  "jsonrpc": "2.0",
  "method": "account/search",
  "params": {
    "authToken": $SYSPASS_TOKEN_SEARCH,
    "tokenPass": $SYSPASS_PASS_SEARCH,
    "text": "ies2.cc-staging.site",   # search by hostname
  },
  "id": 1
}
# Returns: list of accounts with {id, login, url, name, ...}
# Multiple accounts per site are typical (Drupal admin + Basic HTTP Auth + DB + ...).
# Filter by `name` field to disambiguate: "Drupal" → admin login; "Basic HTTP Auth" → Traefik gate.

# Step 2: account/viewPass with the filtered id
viewpass_response = {
  "jsonrpc": "2.0",
  "method": "account/viewPass",
  "params": {
    "authToken": $SYSPASS_TOKEN_VIEWPASS,
    "tokenPass": $SYSPASS_PASS_VIEWPASS,
    "id": <filtered_account_id>,
  },
  "id": 1
}
# Returns: {"result": {"result": {"password": "<plain>"}}}
```

**Account naming convention observed (2026-05-15):**
- `Basic HTTP Auth` — Traefik gateway credentials (login is usually a single token like `ies`)
- `Drupal` — Drupal admin user (login is usually `compucorp_admin`)
- One pair per site, one pair per environment (staging / data / etc.)

**PII redaction:** passwords are production-equivalent secrets. Never include in Jira comments, PR bodies, logs, or transcripts.

**Helper:** `prompts/repro_helpers.get_syspass_cred(account_search, prefer_name=)` wraps the two-step flow.
```

- [ ] **Step 2: Commit**

```bash
git add prompts/TOOLS.md
git commit -m "Add sysPass section to TOOLS.md (visual-repro support)"
```

---

## Task 3: Helper — `assert_staging_host` (TDD)

**Files:**
- Create: `prompts/repro_helpers.py` (start of module)
- Create: `tests/python/test_repro_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_repro_helpers.py`:

```python
"""Unit tests for prompts/repro_helpers.py"""
import sys
from pathlib import Path

# Make prompts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))

import pytest
import repro_helpers as rh


class TestAssertStagingHost:
    @pytest.mark.parametrize("url", [
        "https://ies2.cc-staging.site",
        "https://ies2.cc-staging.site/some/path",
        "https://www.cc-data.site",
        "https://x.cc-prelive.site",
        "https://y.cc-dev.site",
        "https://abc.cc-test.site",
        "https://abc.public.cc-test.site",
    ])
    def test_accepts_known_staging_patterns(self, url):
        # Should not raise
        rh.assert_staging_host(url)

    @pytest.mark.parametrize("url", [
        "https://www.the-ies.org",                  # production
        "https://example.com",                       # unrelated
        "https://evil.com/cc-staging.site/path",     # path injection
        "http://staging.client.org",                 # custom-domain staging (v2)
        "https://www.civi.plus",                     # civiplus prod
    ])
    def test_refuses_non_staging(self, url):
        with pytest.raises(RuntimeError, match="non-staging"):
            rh.assert_staging_host(url)

    def test_no_escape_hatch_via_env(self, monkeypatch):
        monkeypatch.setenv("SYMPHONY_REPRO_ALLOW_PRODUCTION", "1")
        with pytest.raises(RuntimeError):
            rh.assert_staging_host("https://www.the-ies.org")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mar/projects/compuco-symphony && python3 -m pytest tests/python/test_repro_helpers.py -v
```

Expected: `ModuleNotFoundError: No module named 'repro_helpers'`

- [ ] **Step 3: Create the helper module skeleton + implement `assert_staging_host`**

Create `prompts/repro_helpers.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m pytest tests/python/test_repro_helpers.py::TestAssertStagingHost -v
```

Expected: all parametrized tests pass.

- [ ] **Step 5: Commit**

```bash
git add prompts/repro_helpers.py tests/python/test_repro_helpers.py
git commit -m "Add assert_staging_host helper with hostname-pattern allowlist"
```

---

## Task 4: Helper — `get_syspass_cred` (two-step search + viewPass)

**Files:**
- Modify: `prompts/repro_helpers.py` (append function)
- Modify: `tests/python/test_repro_helpers.py` (append tests)
- Modify: `tests/python/conftest.py` (create — shared fixtures)

- [ ] **Step 1: Create conftest.py with HTTP mock fixture**

Create `tests/python/conftest.py`:

```python
"""Shared pytest fixtures for repro_helpers tests."""
import pytest


@pytest.fixture
def syspass_env(monkeypatch):
    """Set fake sysPass env vars for test isolation."""
    monkeypatch.setenv("SYSPASS_URL", "https://vault.test.local")
    monkeypatch.setenv("SYSPASS_TOKEN_SEARCH", "fake_search_token")
    monkeypatch.setenv("SYSPASS_PASS_SEARCH", "fake_search_pass")
    monkeypatch.setenv("SYSPASS_TOKEN_VIEWPASS", "fake_viewpass_token")
    monkeypatch.setenv("SYSPASS_PASS_VIEWPASS", "fake_viewpass_pass")
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/python/test_repro_helpers.py`:

```python
class TestGetSyspassCred:
    def test_two_step_flow_with_prefer_name(self, syspass_env, monkeypatch):
        """search returns 2 accounts; prefer_name filters to 1; viewPass fetches password."""
        calls = []

        def fake_post(url, json, timeout):
            calls.append(json)
            method = json["method"]
            if method == "account/search":
                return _FakeResponse({
                    "result": {"result": [
                        {"id": 6277, "name": "Basic HTTP Auth", "login": "ies",
                         "url": "https://ies2.cc-staging.site"},
                        {"id": 6278, "name": "Drupal", "login": "compucorp_admin",
                         "url": "https://ies2.cc-staging.site"},
                    ]}
                })
            if method == "account/viewPass":
                assert json["params"]["id"] == 6278
                return _FakeResponse({"result": {"result": {"password": "secret123"}}})
            raise AssertionError(f"unexpected method: {method}")

        monkeypatch.setattr(rh.requests, "post", fake_post)
        cred = rh.get_syspass_cred("ies2.cc-staging.site", prefer_name="Drupal")
        assert cred == {"id": 6278, "login": "compucorp_admin", "name": "Drupal",
                        "url": "https://ies2.cc-staging.site", "password": "secret123"}
        assert len(calls) == 2
        assert calls[0]["method"] == "account/search"
        assert calls[1]["method"] == "account/viewPass"

    def test_raises_on_zero_matches(self, syspass_env, monkeypatch):
        def fake_post(url, json, timeout):
            return _FakeResponse({"result": {"result": []}})
        monkeypatch.setattr(rh.requests, "post", fake_post)
        with pytest.raises(ValueError, match="no sysPass account"):
            rh.get_syspass_cred("nonexistent.site")

    def test_raises_on_ambiguous_match_without_prefer_name(self, syspass_env, monkeypatch):
        def fake_post(url, json, timeout):
            return _FakeResponse({"result": {"result": [
                {"id": 1, "name": "Drupal", "login": "a", "url": "https://x"},
                {"id": 2, "name": "Drupal", "login": "b", "url": "https://x"},
            ]}})
        monkeypatch.setattr(rh.requests, "post", fake_post)
        with pytest.raises(ValueError, match="ambiguous"):
            rh.get_syspass_cred("x")

    def test_prefer_name_case_insensitive(self, syspass_env, monkeypatch):
        def fake_post(url, json, timeout):
            if json["method"] == "account/search":
                return _FakeResponse({"result": {"result": [
                    {"id": 6278, "name": "Drupal", "login": "admin", "url": "https://x"},
                ]}})
            return _FakeResponse({"result": {"result": {"password": "p"}}})
        monkeypatch.setattr(rh.requests, "post", fake_post)
        cred = rh.get_syspass_cred("x", prefer_name="drupal")  # lowercase
        assert cred["id"] == 6278


class _FakeResponse:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
python3 -m pytest tests/python/test_repro_helpers.py::TestGetSyspassCred -v
```

Expected: `AttributeError: module 'repro_helpers' has no attribute 'get_syspass_cred'` (or similar).

- [ ] **Step 4: Implement `get_syspass_cred`**

Append to `prompts/repro_helpers.py`:

```python
# --- Credentials (sysPass) ---

import requests  # type: ignore  # imported here to keep module-level imports thin


def get_syspass_cred(account_search: str, *, prefer_name: str | None = None) -> dict:
    """Fetch a Compucorp sysPass credential via the two-step search→viewPass flow.

    Returns: {id, login, password, url, name}
    SECURITY: caller MUST NOT log the returned dict — password is plain.

    Two-step flow:
      1. account/search with text=<account_search>, auth via SYSPASS_TOKEN_SEARCH
         + SYSPASS_PASS_SEARCH.
      2. If prefer_name is set, filter results case-insensitively on the `name`
         field.
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
    accounts = r.json()["result"]["result"]

    # Step 2: filter by prefer_name (case-insensitive substring)
    if prefer_name:
        pn = prefer_name.lower()
        accounts = [a for a in accounts if pn in (a.get("name") or "").lower()]

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
    password = r.json()["result"]["result"]["password"]

    return {
        "id": acc["id"],
        "login": acc.get("login"),
        "name": acc.get("name"),
        "url": acc.get("url"),
        "password": password,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
python3 -m pytest tests/python/test_repro_helpers.py::TestGetSyspassCred -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add prompts/repro_helpers.py tests/python/test_repro_helpers.py tests/python/conftest.py
git commit -m "Add get_syspass_cred (two-step search→viewPass JSON-RPC)"
```

---

## Task 5: Helper — `basic_auth_context` and `dismiss_cookie_banner`

**Files:**
- Modify: `prompts/repro_helpers.py`
- Modify: `tests/python/test_repro_helpers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/python/test_repro_helpers.py`:

```python
class TestBasicAuthContext:
    def test_passes_credentials_and_viewport(self, syspass_env, monkeypatch):
        """basic_auth_context calls browser.new_context with http_credentials + viewport."""
        called = {}

        class FakeBrowser:
            def new_context(self, **kwargs):
                called.update(kwargs)
                class FakeCtx: pass
                return FakeCtx()

        def fake_viewpass(aid):
            return {"id": aid, "login": "ies", "password": "basic_pass", "name": "Basic HTTP Auth", "url": "x"}

        # Inject a fake viewPass by-id helper for context setup
        monkeypatch.setattr(rh, "_syspass_viewpass_by_id", fake_viewpass)

        rh.basic_auth_context(FakeBrowser(), syspass_account_id=6277)

        assert called["http_credentials"] == {"username": "ies", "password": "basic_pass"}
        assert called["viewport"] == {"width": 1440, "height": 900}


class TestDismissCookieBanner:
    def test_returns_true_when_dismissed(self):
        clicks = []
        class FakeRole:
            def click(self, timeout=None):
                clicks.append("clicked")
        class FakePage:
            def get_by_role(self, role, name=None):
                return FakeRole()

        assert rh.dismiss_cookie_banner(FakePage()) is True
        assert clicks == ["clicked"]

    def test_returns_false_when_no_banner(self):
        class FakeRole:
            def click(self, timeout=None):
                raise Exception("not found")
        class FakePage:
            def get_by_role(self, role, name=None):
                return FakeRole()

        assert rh.dismiss_cookie_banner(FakePage()) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestBasicAuthContext tests/python/test_repro_helpers.py::TestDismissCookieBanner -v`

Expected: AttributeError on missing functions.

- [ ] **Step 3: Implement both functions**

Append to `prompts/repro_helpers.py`:

```python
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
    return {"id": account_id, "password": r.json()["result"]["result"]["password"]}


def basic_auth_context(
    browser: "Browser",
    *,
    syspass_account_id: int,
    viewport: dict | None = None,
) -> "BrowserContext":
    """Playwright context with Traefik Basic Auth + pinned viewport.

    Looks up the Basic Auth credentials by sysPass account id (caller passes
    the id from get_syspass_cred). Returns a context ready for `.new_page()`.
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
```

Note: `basic_auth_context` uses a by-id sysPass call (different from `get_syspass_cred`'s search-first flow) because the caller usually already has the id and wants to skip a redundant search. The test mocks `_syspass_viewpass_by_id` directly.

Refactor `_syspass_viewpass_by_id` is a separate helper; `basic_auth_context` reads only the login and password fields. Make sure the test's fake_viewpass returns `login` field too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestBasicAuthContext tests/python/test_repro_helpers.py::TestDismissCookieBanner -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add prompts/repro_helpers.py tests/python/test_repro_helpers.py
git commit -m "Add basic_auth_context + dismiss_cookie_banner helpers"
```

---

## Task 6: Helper — `compucorp_drupal_login_autodetect` (SSP shape only)

**Files:**
- Modify: `prompts/repro_helpers.py`
- Modify: `tests/python/test_repro_helpers.py`

The empirical validation only proved SSP two-step. Per spec, Cognito-bypass and standard one-step raise NotImplementedError. The first ticket exercising those paths drives validation work.

- [ ] **Step 1: Write the failing test**

Append to `tests/python/test_repro_helpers.py`:

```python
class TestCompucorpDrupalLoginAutodetect:
    def test_ssp_two_step_path(self):
        """SSP form id detected → fill name, click button, wait for password, fill, submit."""
        events = []

        class FakeForm:
            def __init__(self):
                self._first = self
            @property
            def first(self):
                return self
            def locator(self, selector):
                events.append(("form.locator", selector))
                if "input[name='name']" in selector:
                    return _FakeInput(events, "name")
                if "button[type='submit'][name='op']" in selector:
                    return _FakeButton(events, "Next")
                return _FakeLocator(events)
            def is_visible(self):
                return True
            def count(self):
                return 1

        class FakePage:
            def __init__(self):
                self._password_field = None
                self._url = "https://ies2.cc-staging.site/user/login"
            def goto(self, url, **kwargs):
                events.append(("goto", url))
                self._url = url
            def locator(self, selector):
                events.append(("page.locator", selector))
                if "form#ssp-core-user-login-or-register-form" in selector:
                    return FakeForm()
                if "input[type='password']" in selector:
                    return _FakeInput(events, "password")
                if "logout" in selector:
                    return _FakeCounted(1)
                return _FakeLocator(events)
            def get_by_role(self, role, name=None):
                return _FakeRoleClickRaises()  # no cookie banner
            def wait_for_selector(self, selector, **kwargs):
                events.append(("wait_for_selector", selector))
            def wait_for_load_state(self, state, **kwargs):
                events.append(("wait_for_load_state", state))
            def expect_navigation(self, **kwargs):
                return _FakeNavCtx()
            @property
            def url(self):
                return self._url

        rh.compucorp_drupal_login_autodetect(FakePage(), "user", "pass",
                                              try_cognito_bypass=False)

        # Should have navigated to /user/login (no cognito bypass attempted)
        gotos = [e for e in events if e[0] == "goto"]
        assert any("/user/login" in g[1] for g in gotos)
        # Should have filled name, clicked next, waited for password, filled password
        fills = [e for e in events if "fill" in str(e)]
        assert any("user" in str(f) for f in fills)
        assert any("pass" in str(f) for f in fills)


    def test_cognito_path_raises_not_implemented(self):
        """If Cognito bypass URL responds, we currently raise NotImplementedError."""
        # ... see helper docstring; test asserts the NotImplementedError path
        pytest.skip("Cognito-bypass path requires HEAD request mocking; covered when first Cognito site is added")


    def test_standard_one_step_path_raises_not_implemented(self):
        pytest.skip("Standard one-step path: deferred until first non-SSP non-Cognito site appears")


class _FakeInput:
    def __init__(self, events, name):
        self._events = events
        self._name = name
    def fill(self, value):
        self._events.append((f"fill[{self._name}]", value))
    @property
    def first(self):
        return self
    def count(self):
        return 1
    def locator(self, selector):
        if "xpath=ancestor::form" in selector:
            return _FakeForm2(self._events)
        return self


class _FakeForm2:
    def __init__(self, events):
        self._events = events
    @property
    def first(self):
        return self
    def locator(self, selector):
        self._events.append(("ancestor-form.locator", selector))
        return _FakeButton(self._events, "submit")


class _FakeButton:
    def __init__(self, events, name):
        self._events = events
        self._name = name
    @property
    def first(self):
        return self
    def click(self, **kwargs):
        self._events.append((f"click[{self._name}]", None))
    def count(self):
        return 1


class _FakeCounted:
    def __init__(self, n):
        self._n = n
    def count(self):
        return self._n


class _FakeLocator:
    def __init__(self, events):
        self._events = events
    @property
    def first(self):
        return self
    def count(self):
        return 0
    def is_visible(self):
        return False


class _FakeRoleClickRaises:
    def click(self, timeout=None):
        raise Exception("no banner")


class _FakeNavCtx:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False  # don't suppress
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestCompucorpDrupalLoginAutodetect::test_ssp_two_step_path -v`

Expected: AttributeError on `compucorp_drupal_login_autodetect`.

- [ ] **Step 3: Implement the helper (SSP-only; raise NotImplementedError for others)**

Append to `prompts/repro_helpers.py`:

```python
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

    # Step 2: navigate to /user/login (the SSP form action)
    if "/user/login" not in page.url:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestCompucorpDrupalLoginAutodetect -v`

Expected: `test_ssp_two_step_path` passes; the other two are SKIPPED (not failed).

- [ ] **Step 5: Commit**

```bash
git add prompts/repro_helpers.py tests/python/test_repro_helpers.py
git commit -m "Add compucorp_drupal_login_autodetect (SSP shape only; others NotImplementedError)"
```

---

## Task 7: Helper — `lifecycle_test_user` (context manager + create + cancel + find_uid)

**Files:**
- Modify: `prompts/repro_helpers.py`
- Modify: `tests/python/test_repro_helpers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/python/test_repro_helpers.py`:

```python
class TestLifecycleTestUser:
    def test_create_and_cleanup_via_admin_uri(self):
        """Enter creates via /admin/people/create, exit cancels via /user/<uid>/cancel."""
        events = []

        class FakePage:
            def __init__(self):
                self._url = "https://x.cc-staging.site/"
                self._after_create = False
            def goto(self, url, **kwargs):
                events.append(("goto", url))
                if "/admin/people/create" in url:
                    self._url = url
                elif "/admin/people?user=" in url:
                    # After successful create, the URL changes to /user/<uid>/edit
                    if self._after_create:
                        self._url = "https://x.cc-staging.site/user/118/edit"
                    else:
                        self._url = url
                elif "/cancel" in url:
                    self._url = url
            def fill(self, sel, val):
                events.append(("fill", sel, val))
            def click(self, sel, **kwargs):
                events.append(("click", sel))
                if "edit-submit" in sel and not self._after_create:
                    self._after_create = True
                    self._url = "https://x.cc-staging.site/user/118/edit"
            def expect_navigation(self, **kwargs):
                return _FakeNavCtx()
            def locator(self, selector):
                if "input[name='user_cancel_method']" in selector:
                    return _FakeRadio(events)
                if "tr:has-text" in selector:
                    return _FakeRow(events, edit_href="/user/118/edit?destination=...")
                return _FakeLocator(events)
            def wait_for_load_state(self, state, **kwargs):
                pass
            @property
            def url(self):
                return self._url

        admin = FakePage()
        with rh.lifecycle_test_user(admin, "symphony-test-abc",
                                     "pwd", "test@compuco.invalid") as username:
            assert username == "symphony-test-abc"

        gotos = [e[1] for e in events if e[0] == "goto"]
        assert any("/admin/people/create" in g for g in gotos)
        assert any("/cancel" in g for g in gotos)
        # The cancel-method radio should be checked with force=True
        radio_events = [e for e in events if e[0] == "radio_check"]
        assert ("radio_check", "force") in radio_events

    def test_cleanup_runs_on_exception(self):
        """If the with-block raises, cleanup still happens."""
        events = []
        class FakePage:
            def __init__(self):
                self._url = ""
                self._created = False
            def goto(self, url, **kwargs):
                events.append(("goto", url))
                if "/admin/people/create" in url:
                    self._url = url
                if "/cancel" in url:
                    self._url = url
            def fill(self, sel, val):
                pass
            def click(self, sel, **kwargs):
                if "edit-submit" in sel and not self._created:
                    self._created = True
                    self._url = "https://x.cc-staging.site/user/118/edit"
            def expect_navigation(self, **kwargs):
                return _FakeNavCtx()
            def locator(self, selector):
                if "input[name='user_cancel_method']" in selector:
                    return _FakeRadio(events)
                return _FakeLocator(events)
            def wait_for_load_state(self, state, **kwargs):
                pass
            @property
            def url(self):
                return self._url

        with pytest.raises(ValueError, match="boom"):
            with rh.lifecycle_test_user(FakePage(), "symphony-test-xyz", "p", "x@y.z"):
                raise ValueError("boom")

        gotos = [e[1] for e in events if e[0] == "goto"]
        assert any("/cancel" in g for g in gotos), \
            f"cleanup should have navigated to /cancel; got {gotos}"


class _FakeRadio:
    def __init__(self, events):
        self._events = events
    def check(self, force=False):
        self._events.append(("radio_check", "force" if force else "soft"))


class _FakeRow:
    def __init__(self, events, edit_href):
        self._events = events
        self._edit_href = edit_href
    @property
    def first(self):
        return self
    def count(self):
        return 1
    def locator(self, selector):
        return _FakeAnchor(self._edit_href)


class _FakeAnchor:
    def __init__(self, href):
        self._href = href
    @property
    def first(self):
        return self
    def get_attribute(self, name):
        if name == "href":
            return self._href
        return None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestLifecycleTestUser -v`

Expected: AttributeError.

- [ ] **Step 3: Implement the lifecycle + supporting functions**

Append to `prompts/repro_helpers.py`:

```python
# --- Test user lifecycle ---

class UserExistsError(Exception):
    """Raised when creating a test user that already exists."""


class CreateUserError(Exception):
    """Raised when /admin/people/create submit fails for any other reason."""


_UID_FROM_URL_RE = re.compile(r"/user/(\d+)/edit")


def create_test_user(admin_page: "Page", *, username: str, email: str,
                     password: str) -> int:
    """Create a non-admin test user via /admin/people/create.

    Returns the new user's uid (extracted from the redirect URL).
    Raises UserExistsError if the username is already taken,
    CreateUserError on other failures.
    """
    site_root = admin_page.url.rsplit("/admin", 1)[0] if "/admin" in admin_page.url \
                else admin_page.url.rstrip("/")
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

    # Check the redirected URL for the new uid
    m = _UID_FROM_URL_RE.search(admin_page.url)
    if m:
        return int(m.group(1))

    # No uid in URL — scrape error messages
    error_text = ""
    try:
        error_text = admin_page.locator(".messages--error, .messages.error") \
                               .inner_text(timeout=2000)
    except Exception:
        pass
    if any(s in error_text.lower() for s in ("already taken", "already registered")):
        raise UserExistsError(f"user {username!r} exists: {error_text}")
    raise CreateUserError(f"failed to create user {username!r}: {error_text!r} url={admin_page.url}")


def find_uid_by_username(admin_page: "Page", username: str) -> int | None:
    """Look up a uid by username via /admin/people?user=<username>.

    Returns None if not found. Used by cleanup when create_test_user partially
    failed (created but uid wasn't captured).
    """
    site_root = admin_page.url.rsplit("/admin", 1)[0] if "/admin" in admin_page.url \
                else admin_page.url.rstrip("/")
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
    site_root = admin_page.url.rsplit("/admin", 1)[0] if "/admin" in admin_page.url \
                else admin_page.url.rstrip("/")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/python/test_repro_helpers.py::TestLifecycleTestUser -v`

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add prompts/repro_helpers.py tests/python/test_repro_helpers.py
git commit -m "Add lifecycle_test_user context manager + create/cancel/find helpers"
```

---

## Task 8: Integration smoke test against IES2 staging

**Files:**
- Create: `tests/python/integration/test_repro_smoke.py`

This is gated on env vars (`SYSPASS_*` and live network) and marked `@pytest.mark.integration` so CI doesn't try to run it without setup.

- [ ] **Step 1: Write the smoke test**

Create `tests/python/integration/test_repro_smoke.py`:

```python
"""Integration smoke test: full visual-repro flow against ies2.cc-staging.site.

REQUIRES:
  - SYSPASS_URL, SYSPASS_TOKEN_SEARCH/PASS_SEARCH, SYSPASS_TOKEN_VIEWPASS/PASS_VIEWPASS
  - Network access to vault.cc-infra.tools and ies2.cc-staging.site
  - playwright + chromium installed (run `playwright install chromium`)

Run: python3 -m pytest tests/python/integration/test_repro_smoke.py -v -m integration
"""
import os
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "prompts"))
import repro_helpers as rh

# Skip the whole module if env not set
pytestmark = pytest.mark.integration

SITE = "https://ies2.cc-staging.site"


@pytest.fixture(scope="module")
def syspass_required():
    required = ["SYSPASS_URL", "SYSPASS_TOKEN_SEARCH", "SYSPASS_PASS_SEARCH",
                "SYSPASS_TOKEN_VIEWPASS", "SYSPASS_PASS_VIEWPASS"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        pytest.skip(f"missing env vars: {missing}")


def test_get_syspass_cred_search_flow(syspass_required):
    """Validates the two-step search→viewPass flow (was hard-coded id in earlier test)."""
    cred = rh.get_syspass_cred("ies2.cc-staging.site", prefer_name="Drupal")
    assert cred["id"] == 6278
    assert cred["login"] == "compucorp_admin"
    assert len(cred["password"]) > 0
    assert "Drupal" in (cred.get("name") or "")


def test_assert_staging_host_accepts_ies2(syspass_required):
    rh.assert_staging_host(SITE)  # should not raise


def test_full_repro_with_random_suffix_username(syspass_required, tmp_path):
    """End-to-end: validates the random-suffix username + helper module."""
    from playwright.sync_api import sync_playwright

    rh.assert_staging_host(SITE)
    basic = rh.get_syspass_cred(SITE, prefer_name="Basic HTTP Auth")
    admin = rh.get_syspass_cred(SITE, prefer_name="Drupal")
    test_username = f"symphony-test-{secrets.token_hex(3)}"
    test_password = "Sym!" + secrets.token_urlsafe(12)

    screenshot_path = tmp_path / "before.png"
    screenshot_path.unlink(missing_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # Admin context
            admin_ctx = rh.basic_auth_context(browser, syspass_account_id=basic["id"])
            admin_page = admin_ctx.new_page()
            rh.compucorp_drupal_login_autodetect(
                admin_page, admin["login"], admin["password"], try_cognito_bypass=False)

            with rh.lifecycle_test_user(admin_page, test_username, test_password,
                                         f"{test_username}@compuco.invalid"):
                # Test user — session 1
                ctx1 = rh.basic_auth_context(browser, syspass_account_id=basic["id"])
                p1 = ctx1.new_page()
                rh.compucorp_drupal_login_autodetect(
                    p1, test_username, test_password, try_cognito_bypass=False)

                # Test user — session 2 (triggers session limit)
                ctx2 = rh.basic_auth_context(browser, syspass_account_id=basic["id"])
                p2 = ctx2.new_page()
                rh.compucorp_drupal_login_autodetect(
                    p2, test_username, test_password, try_cognito_bypass=False)

                assert "/session/limit" in p2.url, \
                    f"expected /session/limit; got {p2.url}"
                p2.screenshot(path=str(screenshot_path), full_page=True)
        finally:
            browser.close()

    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 10_000, "screenshot suspiciously small"
```

- [ ] **Step 2: Run the smoke test**

Make sure `SYSPASS_*` env vars are set in the shell (or `start-symphony.sh`-style export them):
```bash
cd /Users/mar/projects/compuco-symphony && python3 -m pytest tests/python/integration/test_repro_smoke.py -v -m integration
```

Expected: all 3 tests pass. The full E2E test takes ~30-60 seconds.

If `compucorp_drupal_login_autodetect` raises `NotImplementedError` on the test user logins — that means the SSP detection isn't matching for the new user. Inspect; likely the SSP form is rendered the same way for any user. If it's a genuine difference, file as a follow-up and adjust the helper.

- [ ] **Step 3: Commit**

```bash
git add tests/python/integration/test_repro_smoke.py
git commit -m "Add integration smoke test for visual-repro full flow"
```

---

## Task 9: Write `prompts/visual-repro.md`

**Files:**
- Create: `prompts/visual-repro.md`

This is the operational doc the agent reads at WORKFLOW step 10.

- [ ] **Step 1: Write the full doc**

Create `prompts/visual-repro.md` with the full content (long file — see spec § Operational doc for structure). The content below is the complete file:

```markdown
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
                page, admin["login"], admin["password"], try_cognito_bypass=False)
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
                admin_page, admin["login"], admin["password"], try_cognito_bypass=False)

            with lifecycle_test_user(admin_page, test_username, test_password,
                                     f"{test_username}@compuco.invalid"):
                ctx1 = basic_auth_context(browser, syspass_account_id=basic["id"])
                p1 = ctx1.new_page()
                compucorp_drupal_login_autodetect(
                    p1, test_username, test_password, try_cognito_bypass=False)

                ctx2 = basic_auth_context(browser, syspass_account_id=basic["id"])
                p2 = ctx2.new_page()
                compucorp_drupal_login_autodetect(
                    p2, test_username, test_password, try_cognito_bypass=False)

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

## 7. File locations and PR embedding (v1)

- `repro.py` lives at `<workspace>/repro.py` (workspace root, NOT inside `./repo/`).
- `before.png` lives at `<workspace>/before.png`.
- Neither file is committed to the client repo in v1.
- PR `## Before` section (when reproduction succeeded):
  > Reproduction completed; programmatic assertion fired. Screenshot at `~/symphony_workspaces/<KEY>/before.png` on the Symphony host. Re-run via `python3 ~/symphony_workspaces/<KEY>/repro.py`.
- Direct PR-body image embedding is deferred to v2.
```

- [ ] **Step 2: Commit**

```bash
git add prompts/visual-repro.md
git commit -m "Add prompts/visual-repro.md operational doc for visual reproduction"
```

---

## Task 10: Update `WORKFLOW.md` step 10 + DRY-RUN summary

**Files:**
- Modify: `WORKFLOW.md` (replace step 10 + add bullet to DRY-RUN OVERRIDE summary)

- [ ] **Step 1: Read the current step 10**

```bash
sed -n '265,275p' WORKFLOW.md
```

(Verify the current step 10 wording before replacing — the exact line numbers may have shifted.)

- [ ] **Step 2: Replace step 10**

Use `Edit` tool to replace the current step 10 text in WORKFLOW.md with:

```
10. **Visual verification (UI-changing PRs).** Apply the three-condition gate from `prompts/visual-repro.md` § 1: (a) diff touches `*.tpl/*.scss/*.css/themes/*.theme/dist`, AND (b) a specific staging URL is resolvable from the ticket (description, comments, or via step 3b Mongo lookup), AND (c) the URL passes `assert_staging_host`. If any condition fails, document the gate decision in PR `## Comments` (one line: "Visual repro skipped: <reason>") and proceed to step 11 with `## Manual verification required` in the PR body.

    When all three conditions hold:

    10a. Read `prompts/visual-repro.md`.
    10b. Pick the simplest pattern (1/2/3) that fits the bug; copy the skeleton to `<workspace>/repro.py` (workspace root — NOT inside `./repo/`).
    10c. Fill `reproduce(page)` and `assert_bug_reproduced(page)`. First line of `main()` must be `pathlib.Path("before.png").unlink(missing_ok=True)`.
    10d. Run: `python3 <workspace>/repro.py`. Outputs `<workspace>/before.png` on success.
    10e. If exit 0 AND `before.png` exists: PR `## Before` reads:
         > "Reproduction completed; programmatic assertion fired. Screenshot at `~/symphony_workspaces/{{ issue.identifier }}/before.png` on the Symphony host. Re-run via `python3 ~/symphony_workspaces/{{ issue.identifier }}/repro.py`."

         Else: PR body gets `## Manual verification required` with explicit reproduction steps (URL, preconditions, what to look for).
    10f. Neither `repro.py` nor `before.png` is committed to the client repo in v1 — audit trail lives in workspace + Symphony's JSONL transcript.
```

- [ ] **Step 3: Find the DRY-RUN OVERRIDE summary template**

```bash
grep -n "dry-run-summary.md" WORKFLOW.md
```

Find the bulleted list of what the summary should contain (currently has 5 bullets a-e).

- [ ] **Step 4: Add a 6th bullet to the DRY-RUN summary template**

Use `Edit` to add a new bullet at the end of the existing summary content list:

```
- (f) Visual-repro outcome — one of:
  - `committed-repro` (script ran, assertion fired, before.png at <workspace>/before.png)
  - `gate-skipped` (gate condition failed; reason)
  - `assertion-failed` (script ran but assert_bug_reproduced didn't fire)
  - `host-not-allowlisted` (assert_staging_host refused)
```

- [ ] **Step 5: Commit**

```bash
git add WORKFLOW.md
git commit -m "WORKFLOW step 10: replace with visual-repro procedure (three-condition gate)"
```

---

## Task 11: Extend `prompts/code-reviewer.md` with visual-repro invariants

**Files:**
- Modify: `prompts/code-reviewer.md` (append section)

- [ ] **Step 1: Read the current end of code-reviewer.md**

```bash
tail -40 prompts/code-reviewer.md
```

Identify the right insertion point — typically just before the JSON schema reference or just before the closing instruction block.

- [ ] **Step 2: Append the visual-repro invariants**

Append to `prompts/code-reviewer.md`:

```markdown
## Visual-repro invariants (when workspace contains `repro.py`)

If the agent invoked the visual-repro skill, the workspace will contain `<workspace>/repro.py` (and on success `<workspace>/before.png`). Additionally check:

1. **First function call** in `repro.py` (after imports + module-level constant assignments like `SITE = "..."`) is `assert_staging_host(SITE)`. **BLOCKER** if absent.
2. **`assert_bug_reproduced(page)`** is defined as a function AND is called immediately before any `page.screenshot(path="before.png", ...)` call. **BLOCKER** if missing, undefined, or called after the screenshot.
3. **Cleanup of any test user created via `lifecycle_test_user`** happens via the context manager (its `__exit__` is guaranteed on normal exceptions) OR via an explicit `finally:` block. **BLOCKER** if neither.

The reviewer uses the existing JSON output schema; new findings have `file="repro.py"`.

If `repro.py` is absent (gate didn't fire, or skill skipped), no extra checks needed — review proceeds as usual.
```

- [ ] **Step 3: Commit**

```bash
git add prompts/code-reviewer.md
git commit -m "code-reviewer: enforce visual-repro invariants when repro.py present"
```

---

## Task 12: End-to-end dry-run smoke against a real ticket

This is a manual operator-driven verification, not automated. Goal: confirm Symphony picks up a ticket labelled `agent:todo + agent:dry-run`, invokes WORKFLOW step 10, agent writes valid `repro.py`, runs it, captures `before.png`, and the dry-run-summary records `committed-repro` status.

**Pre-requisites:**
- All previous tasks landed and pushed
- `agent:dry-run` label exists in Jira (will be created when first applied)
- A test ticket targeting a UI bug on a staging site in the allowlist

- [ ] **Step 1: Pick a test ticket**

Options:
- Re-use IESBUILD-267 (label-cycle: ensure `AGENT_DONE` from previous run is removed)
- Create a new test ticket with a known UI bug

For the re-use path:
```bash
rm -f ~/symphony_workspaces/IESBUILD-267/AGENT_DONE
```

- [ ] **Step 2: Apply labels via Atlassian MCP**

Apply both `agent:todo` AND `agent:dry-run` to the test ticket.

- [ ] **Step 3: Start Symphony and observe**

```bash
cd /Users/mar/projects/compuco-symphony && ./start-symphony.sh
```

Watch the log for: agent dispatch → reaches step 10 → reads `visual-repro.md` → writes `repro.py` → runs it → captures `before.png` → writes `dry-run-summary.md` with `committed-repro` status.

- [ ] **Step 4: Validate workspace artifacts**

After agent completes:
```bash
ls -la ~/symphony_workspaces/<KEY>/
# Expected: repro.py, before.png, plan.md, review-result-r1.json, dry-run-summary.md, AGENT_DONE
```

```bash
cat ~/symphony_workspaces/<KEY>/dry-run-summary.md
# Expected: includes (f) "Visual-repro outcome: committed-repro"
```

```bash
file ~/symphony_workspaces/<KEY>/before.png
# Expected: PNG image data, ... 1440 x N, ...
```

- [ ] **Step 5: Re-run the script manually for re-runnability validation**

```bash
cd ~/symphony_workspaces/<KEY> && python3 repro.py
```

Expected: exits 0, regenerates `before.png`. Validates the audit-replay property.

- [ ] **Step 6: Document outcome**

If the smoke passes: write a short note in commit message confirming. No further code changes needed.

If it fails at any step: capture the failure mode, file as a follow-up issue, fix or document workaround. Common expected issues based on empirical run:
- Helper finds Cognito redirect on a non-IES site → `NotImplementedError`, fall through OK
- `dismiss_cookie_banner` returns False on a non-Compucorp banner → non-fatal, OK
- Test user creation fails due to existing `symphony-test-*` orphans → manual sweep, then retry

- [ ] **Step 7: Final commit (only if changes were needed)**

If any tweaks fell out of the smoke test, commit them. Otherwise, the implementation is complete.

```bash
# only if needed
git commit -m "fix: <specific issue> discovered during end-to-end smoke"
```

---

## Self-review checklist (already done by author at plan time)

- ✅ Spec coverage: every section of the spec has a task (helpers Task 3-7, TOOLS.md Task 2, visual-repro.md Task 9, WORKFLOW Task 10, code-reviewer Task 11, integration Task 8, dry-run smoke Task 12)
- ✅ No placeholders: every code step shows actual code; every command step shows actual command + expected output
- ✅ Type consistency: helper function signatures used in patterns match the helper definitions
- ✅ Prerequisites from spec § Implementation prerequisites mapped to tasks: TOOLS.md sysPass (Task 2), search-flow validation (Task 8), random-suffix validation (Task 8), DRY-RUN summary (Task 10), reviewer N=3 baked-in via skeletons (Task 9)
