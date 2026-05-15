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
