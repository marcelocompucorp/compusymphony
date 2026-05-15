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
