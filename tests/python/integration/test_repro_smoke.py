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
    """Validates the two-step search->viewPass flow (was hard-coded id in earlier test)."""
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
                admin_page, admin["login"], admin["password"], site=SITE, try_cognito_bypass=False)

            with rh.lifecycle_test_user(admin_page, test_username, test_password,
                                         f"{test_username}@compuco.invalid"):
                # Test user - session 1
                ctx1 = rh.basic_auth_context(browser, syspass_account_id=basic["id"])
                p1 = ctx1.new_page()
                rh.compucorp_drupal_login_autodetect(
                    p1, test_username, test_password, site=SITE, try_cognito_bypass=False)

                # Test user - session 2 (triggers session limit)
                ctx2 = rh.basic_auth_context(browser, syspass_account_id=basic["id"])
                p2 = ctx2.new_page()
                rh.compucorp_drupal_login_autodetect(
                    p2, test_username, test_password, site=SITE, try_cognito_bypass=False)

                assert "/session/limit" in p2.url, \
                    f"expected /session/limit; got {p2.url}"
                p2.screenshot(path=str(screenshot_path), full_page=True)
        finally:
            browser.close()

    assert screenshot_path.exists()
    assert screenshot_path.stat().st_size > 10_000, "screenshot suspiciously small"
