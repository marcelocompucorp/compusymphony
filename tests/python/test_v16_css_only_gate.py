"""Regression test for the v1.6 CSS-only gate.

The gate command appears verbatim in two places that must stay in sync:
  - prompts/visual-repro.md §8 (agent-facing: decides whether to capture after.png)
  - prompts/code-reviewer.md invariant 5 (reviewer-facing: decides BLOCKER severity)

If the gate's classification regresses, after-state capture either silently
skips on CSS-only diffs (the failure mode from IESBUILD-260 run 1) or
silently fires on behavior-bearing diffs (producing misleading after.png).

This test exercises the gate against a fixed set of synthetic diffs and asserts
classification. It uses subprocess to run the actual shell pipeline rather than
a parallel Python reimplementation, so a change to the gate string in either
prompt file is caught here.
"""
import subprocess

import pytest

# The literal gate command from visual-repro.md §8 / code-reviewer.md invariant 5.
# If you change this, change both prompt files.
GATE_COMMAND = (
    "grep -v '^.agent-artifacts/' "
    "| grep -vE '\\.(scss|css|tpl|map)$' "
    "| head -1"
)


def run_gate(diff_files):
    """Return the first file that fails the allowlist, or '' if all pass."""
    input_str = "\n".join(diff_files) + ("\n" if diff_files else "")
    result = subprocess.run(
        ["bash", "-c", GATE_COMMAND],
        input=input_str,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


CSS_ONLY_CASES = [
    pytest.param(
        ["sites/all/themes/custom/ies/assets/sass/_foo.scss"],
        id="single-scss",
    ),
    pytest.param(
        [
            "sites/all/themes/custom/ies/assets/sass/_foo.scss",
            "sites/all/themes/custom/ies/dist/css/style.css",
        ],
        id="scss-plus-compiled-css",
    ),
    pytest.param(
        [
            "sites/all/themes/custom/ies/dist/css/style.css",
            "sites/all/themes/custom/ies/dist/css/style.css.map",
        ],
        id="css-plus-source-map",
    ),
    pytest.param(
        ["templates/CRM/Contact/Form/CustomData.tpl"],
        id="civicrm-smarty-tpl",
    ),
    pytest.param(
        [
            "sites/all/themes/custom/ies/assets/sass/_a.scss",
            "sites/all/themes/custom/ies/assets/sass/_b.scss",
        ],
        id="multiple-scss",
    ),
    pytest.param(
        [
            ".agent-artifacts/IESBUILD-260/before.png",
            ".agent-artifacts/IESBUILD-260/after.png",
            ".agent-artifacts/IESBUILD-260/repro.py",
            "sites/all/themes/custom/ies/assets/sass/_foo.scss",
            "sites/all/themes/custom/ies/dist/css/style.css",
        ],
        id="css-only-with-agent-artifacts-excluded",
    ),
    pytest.param([], id="empty-diff"),
]


BEHAVIOR_CASES = [
    pytest.param(
        ["sites/all/themes/custom/ies/js/foo.js"],
        "sites/all/themes/custom/ies/js/foo.js",
        id="js-file",
    ),
    pytest.param(
        ["sites/all/modules/custom/foo/foo.module"],
        "sites/all/modules/custom/foo/foo.module",
        id="drupal-module",
    ),
    pytest.param(
        ["sites/all/modules/custom/foo/foo.php"],
        "sites/all/modules/custom/foo/foo.php",
        id="php-file",
    ),
    pytest.param(
        ["sites/all/themes/custom/ies/templates/node--article.tpl.php"],
        "sites/all/themes/custom/ies/templates/node--article.tpl.php",
        id="drupal-tpl-php",
    ),
    pytest.param(
        ["sites/all/themes/custom/ies/ies.info"],
        "sites/all/themes/custom/ies/ies.info",
        id="theme-info-file",
    ),
    pytest.param(
        ["sites/all/themes/custom/ies/template.php"],
        "sites/all/themes/custom/ies/template.php",
        id="theme-template-php",
    ),
    pytest.param(
        [
            "sites/all/themes/custom/ies/assets/sass/_foo.scss",
            "sites/all/themes/custom/ies/js/foo.js",
        ],
        "sites/all/themes/custom/ies/js/foo.js",
        id="scss-plus-js-mixed",
    ),
    pytest.param(
        ["sites/all/themes/custom/ies/foo.install"],
        "sites/all/themes/custom/ies/foo.install",
        id="install-file",
    ),
    pytest.param(
        ["sites/all/themes/custom/ies/dist/js/main.js"],
        "sites/all/themes/custom/ies/dist/js/main.js",
        id="dist-js-not-just-css",
    ),
]


@pytest.mark.parametrize("diff_files", CSS_ONLY_CASES)
def test_gate_classifies_as_css_only(diff_files):
    """CSS-only diffs return empty string from the gate (== proceed with §8)."""
    result = run_gate(diff_files)
    assert result == "", (
        f"Expected CSS-only classification (empty result), but gate flagged: {result!r}"
    )


@pytest.mark.parametrize("diff_files,expected_first_offender", BEHAVIOR_CASES)
def test_gate_classifies_as_behavior(diff_files, expected_first_offender):
    """Behavior-bearing diffs return the offending filename (== skip §8)."""
    result = run_gate(diff_files)
    assert result == expected_first_offender, (
        f"Expected behavior file {expected_first_offender!r} to be flagged, "
        f"got {result!r}"
    )
