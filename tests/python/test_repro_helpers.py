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
