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
