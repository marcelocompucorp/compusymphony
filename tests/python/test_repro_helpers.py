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
