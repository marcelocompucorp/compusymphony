"""Tests for analyze-run.py:validate_agent_done (v1.12 schema coverage).

Covers all valid AGENT_DONE prefixes introduced in v1.11/v1.12:
  success, success-dual, success-upstream-only, dry-run,
  blocked-review, blocked-verify, blocked

And all malformed-input failure paths:
  - bad prefix
  - missing timestamp
  - missing issue identifier
  - malformed timestamp
  - multi-line content
  - empty file
  - missing file
"""

import os
import sys
import tempfile

import pytest

# Add the project root to sys.path so we can import analyze-run.py as a module.
# analyze-run.py uses a hyphen which prevents normal `import`, so we import
# with importlib.
import importlib.util

_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_ANALYZE_RUN_PATH = os.path.join(_PROJECT_ROOT, "analyze-run.py")

spec = importlib.util.spec_from_file_location("analyze_run", _ANALYZE_RUN_PATH)
_analyze_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_analyze_run)

validate_agent_done = _analyze_run.validate_agent_done


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_done(tmp_dir, content):
    """Write content to <tmp_dir>/AGENT_DONE and return the tmp_dir path."""
    path = os.path.join(tmp_dir, "AGENT_DONE")
    with open(path, "w") as fh:
        fh.write(content)
    return tmp_dir


# ---------------------------------------------------------------------------
# Valid prefixes (v1.12 complete set)
# ---------------------------------------------------------------------------

VALID_PREFIXES = [
    "success",
    "success-dual",
    "success-upstream-only",
    "dry-run",
    "blocked-review",
    "blocked-verify",
    "blocked",
]

_VALID_TIMESTAMP = "2026-05-19T10:21:50Z"
_VALID_KEY = "IESBUILD-242"


@pytest.mark.parametrize("prefix", VALID_PREFIXES)
def test_valid_prefix(prefix, tmp_path):
    content = f"{prefix} {_VALID_TIMESTAMP} {_VALID_KEY}\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key=_VALID_KEY)
    assert findings == [], f"Unexpected findings for prefix {prefix!r}: {findings}"


# ---------------------------------------------------------------------------
# Valid timestamp formats
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ts", [
    "2026-05-19T10:21:50Z",
    "2026-05-19T10:21:50+00:00",
    "2026-05-19T10:21:50-05:00",
    "2026-05-19T10:21:50.123Z",
    "2026-05-19T10:21:50.123456Z",
])
def test_valid_timestamp_formats(ts, tmp_path):
    content = f"success {ts} IESBUILD-100\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-100")
    assert findings == [], f"Unexpected findings for timestamp {ts!r}: {findings}"


# ---------------------------------------------------------------------------
# Issue key matching
# ---------------------------------------------------------------------------

def test_key_match_succeeds(tmp_path):
    content = f"success {_VALID_TIMESTAMP} MMMM-999\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="MMMM-999")
    assert findings == []


def test_key_mismatch_flagged(tmp_path):
    content = f"success {_VALID_TIMESTAMP} MMMM-999\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-242")
    assert any("KEY_MISMATCH" in f for f in findings), findings


def test_no_expected_key_skips_check(tmp_path):
    content = f"success {_VALID_TIMESTAMP} MMMM-999\n"
    _write_done(str(tmp_path), content)
    # When expected_issue_key is None (default), no KEY_MISMATCH check runs.
    findings = validate_agent_done(str(tmp_path))
    assert not any("KEY_MISMATCH" in f for f in findings), findings


# ---------------------------------------------------------------------------
# Invalid prefix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_prefix", [
    "SUCCESS",
    "ok",
    "done",
    "complete",
    "success_dual",       # underscore instead of hyphen
    "blocked_review",
    "success-DUAL",
    "",
    "partial",
])
def test_bad_prefix_flagged(bad_prefix, tmp_path):
    content = f"{bad_prefix} {_VALID_TIMESTAMP} IESBUILD-242\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-242")
    # Either BAD_PREFIX or MALFORMED (empty prefix collapses field count)
    assert any("BAD_PREFIX" in f or "MALFORMED" in f for f in findings), (
        f"Expected BAD_PREFIX/MALFORMED finding for prefix {bad_prefix!r}, got: {findings}"
    )


# ---------------------------------------------------------------------------
# Malformed timestamp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_ts", [
    "2026-05-19",                    # date only
    "10:21:50Z",                     # time only
    "2026-05-19 10:21:50Z",          # space separator instead of T
    "2026-05-19T10:21:50",           # missing timezone
    "not-a-timestamp",
])
def test_bad_timestamp_flagged(bad_ts, tmp_path):
    content = f"success {bad_ts} IESBUILD-242\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-242")
    assert any("BAD_TIMESTAMP" in f or "MALFORMED" in f for f in findings), (
        f"Expected BAD_TIMESTAMP/MALFORMED for ts {bad_ts!r}, got: {findings}"
    )


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------

def test_only_prefix_field(tmp_path):
    content = "success\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path))
    assert any("MALFORMED" in f for f in findings), findings


def test_only_prefix_and_timestamp(tmp_path):
    content = f"success {_VALID_TIMESTAMP}\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path))
    assert any("MALFORMED" in f for f in findings), findings


def test_four_fields_flagged(tmp_path):
    content = f"success {_VALID_TIMESTAMP} IESBUILD-242 extra\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path))
    assert any("MALFORMED" in f for f in findings), findings


# ---------------------------------------------------------------------------
# File-level edge cases
# ---------------------------------------------------------------------------

def test_invalid_month_not_caught_by_regex(tmp_path):
    """The regex validates ISO-8601 FORMAT only (not calendar validity).
    Month 13 passes format checks — this is a known limitation, documented
    here so it doesn't become a false failure in future regex tightening.
    """
    content = f"success 2026-13-19T10:21:50Z IESBUILD-242\n"
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-242")
    # Expect no findings — regex accepts this (format-valid, calendar-invalid)
    assert findings == [], (
        "Regex now rejects calendar-invalid timestamps — update this test "
        "if stricter validation was intentionally added."
    )


def test_missing_file_flagged(tmp_path):
    findings = validate_agent_done(str(tmp_path))
    assert any("MISSING" in f for f in findings), findings


def test_empty_file_flagged(tmp_path):
    content = ""
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path))
    # Empty file → 0 lines → MALFORMED
    assert findings, "Expected at least one finding for empty file"


def test_multi_line_flagged(tmp_path):
    content = (
        f"success {_VALID_TIMESTAMP} IESBUILD-242\n"
        "extra line\n"
    )
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path))
    assert any("MULTI_LINE" in f for f in findings), findings


# ---------------------------------------------------------------------------
# Missing trailing newline (should be flagged but not fatal)
# ---------------------------------------------------------------------------

def test_missing_trailing_newline_flagged(tmp_path):
    content = f"success {_VALID_TIMESTAMP} IESBUILD-242"  # no trailing \n
    _write_done(str(tmp_path), content)
    findings = validate_agent_done(str(tmp_path), expected_issue_key="IESBUILD-242")
    assert any("NEWLINE" in f or "newline" in f.lower() for f in findings), (
        f"Expected trailing-newline finding, got: {findings}"
    )
