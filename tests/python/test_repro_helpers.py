"""Unit tests for prompts/repro_helpers.py"""
import sys
from pathlib import Path

# Make prompts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))

import pytest
import requests

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

    def test_raises_on_syspass_error_envelope(self, syspass_env, monkeypatch):
        """sysPass returns {'error': {...}} on auth fail / not-found."""
        def fake_post(url, json, timeout):
            return _FakeResponse({"error": {"code": -32100, "message": "Account not found"}})
        monkeypatch.setattr(rh.requests, "post", fake_post)
        with pytest.raises(RuntimeError, match="sysPass API error"):
            rh.get_syspass_cred("anything")

    def test_raises_on_http_error(self, syspass_env, monkeypatch):
        """requests.raise_for_status surfaces HTTP failures."""
        class _Boom:
            def raise_for_status(self):
                raise requests.HTTPError("500 Server Error")
            def json(self):
                return {}
        # Need to import requests at module level for HTTPError
        import requests as _r
        def fake_post(url, json, timeout):
            return _Boom()
        monkeypatch.setattr(rh.requests, "post", fake_post)
        with pytest.raises(_r.HTTPError):
            rh.get_syspass_cred("anything")


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


class _FakeResponse:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data


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

    def test_site_root_handles_url_with_path(self):
        """site_root must strip path/query — handles post-create URL /user/118/edit."""
        assert rh._site_root("https://x.cc-staging.site/admin/people/create") \
               == "https://x.cc-staging.site"
        assert rh._site_root("https://x.cc-staging.site/user/118/edit") \
               == "https://x.cc-staging.site"
        assert rh._site_root("https://x.cc-staging.site/admin/people?user=foo") \
               == "https://x.cc-staging.site"
        assert rh._site_root("https://x.cc-staging.site/") \
               == "https://x.cc-staging.site"


class _FakeRadio:
    def __init__(self, events):
        self._events = events
    def count(self):
        return 1
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
