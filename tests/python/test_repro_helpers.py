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
