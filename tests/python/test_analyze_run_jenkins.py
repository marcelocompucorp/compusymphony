"""Tests for the Jenkins-writes audit detector in analyze-run.py.

The detector scans the list of recorded Bash commands and partitions any
Jenkins write attempt (POST/PUT/DELETE/etc.) into:
  - allowed: writes to one of the two carved-out job paths:
      Phase A — `Create Dev Site - Client Specific`
      Phase B — `_Release Dev Site`
  - disallowed: any other Jenkins write — a workflow violation per WORKFLOW.md invariant #5
"""
import sys
from pathlib import Path

# Make analyze-run.py importable. It's at the repo root with a hyphen in the
# filename, so use importlib.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "analyze_run",
    Path(__file__).resolve().parents[2] / "analyze-run.py",
)
analyze_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_run)


def _bc(cmd, idx=0, desc=""):
    """Build a (idx, desc, cmd) tuple matching analyze.py's bash_commands shape."""
    return (idx, desc, cmd)


_CREATE_JOB_PATH = analyze_run.JENKINS_DEVSITE_JOB_PATH_SUBSTR


class TestDetectJenkinsWrites:
    def test_allowed_path_curl_post(self):
        """A curl -X POST to the carved-out job path counts as allowed."""
        cmds = [
            _bc(
                f'curl -sS -X POST -u "$JENKINS_USER:$JENKINS_TOKEN" '
                f'"$JENKINS_URL{_CREATE_JOB_PATH}/buildWithParameters" '
                '--data-urlencode git_repo=...',
                idx=100,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1
        assert result["allowed"][0][0] == 100
        assert result["disallowed"] == []

    def test_disallowed_other_jenkins_post(self):
        """POST to any other Jenkins job is a workflow violation."""
        cmds = [
            _bc(
                'curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN '
                '"$JENKINS_URL/job/Live%20Sites%20-%20Compucontainer/'
                'job/some-prod-deploy/build"',
                idx=42,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1
        assert result["disallowed"][0][0] == 42

    def test_read_only_curl_not_flagged(self):
        """GET requests to Jenkins are not writes — must not appear in either bucket."""
        cmds = [
            _bc(
                'curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" '
                '"$JENKINS_URL/job/Deployments/job/Whatever/api/json"',
                idx=1,
            ),
            _bc(
                'curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" '
                '"$JENKINS_URL/job/Deployments/job/X/consoleText"',
                idx=2,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert result["disallowed"] == []

    def test_python_requests_post_to_allowed_path(self):
        """When the agent invokes the helper via Python, the Bash command
        contains `requests.post(...)` + the allowed job path. Still allowed."""
        cmds = [
            _bc(
                f'python3 -c "import requests; requests.post('
                f"'https://jenkins{_CREATE_JOB_PATH}/buildWithParameters'"
                ', data={...})"',
                idx=200,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1
        assert result["disallowed"] == []

    def test_python_calling_helper_function_allowed(self):
        """Invoking `trigger_dev_site(...)` from repro_helpers also counts as
        an allowed write — the function name itself maps 1:1 to the carved-out
        path by design. (Without this, agents using the helper would appear to
        do no Jenkins POSTs, which would be wrong.)"""
        cmds = [
            _bc(
                'python3 -c "from repro_helpers import trigger_dev_site; '
                'trigger_dev_site(git_repo=\\"g\\", git_tag=\\"t\\", '
                'anondb_url=\\"a\\")"',
                idx=300,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1
        assert result["disallowed"] == []

    def test_jenkins_delete_disallowed_even_on_allowed_path(self):
        """The carve-out is for build TRIGGERS only — not delete/disable.
        DELETE on the dev-site job path is still disallowed."""
        cmds = [
            _bc(
                f'curl -X DELETE -u $JENKINS_USER:$JENKINS_TOKEN '
                f'"$JENKINS_URL{_CREATE_JOB_PATH}/123/"',
                idx=500,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1

    def test_no_jenkins_at_all(self):
        cmds = [_bc("ls -la", idx=1), _bc("git status", idx=2)]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result == {"allowed": [], "disallowed": []}

    def test_echo_with_helper_name_string_not_classified_as_allowed(self):
        """Reviewer-flagged BLOCKER: `echo "trigger_dev_site("` must NOT be
        classified as an allowed Jenkins write — the helper name is only
        meaningful when actually called from Python.
        """
        cmds = [
            _bc('echo "trigger_dev_site(args)"', idx=1),
            _bc('grep -r trigger_dev_site\\( prompts/', idx=2),
            _bc('cat > foo.py <<EOF\ntrigger_dev_site()\nEOF', idx=3),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result == {"allowed": [], "disallowed": []}

    def test_post_to_allowed_path_without_buildWithParameters_is_disallowed(self):
        """Reviewer-flagged: a POST to the allowed job path but a DIFFERENT
        action (e.g. /disable, /123/stop) is NOT what the carve-out permits.
        Only buildWithParameters counts.
        """
        cmds = [
            _bc(
                f'curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN '
                f'"$JENKINS_URL{_CREATE_JOB_PATH}/disable"',
                idx=10,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1


class TestExtractDevsiteTriggerRepo:
    """Reviewer-flagged BLOCKER: audit must cross-check git_repo against
    SITE_DEPLOYABLE_REPOS.
    """

    def test_extracts_repo_from_curl_data_urlencode(self):
        cmd = (
            'curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN '
            '--data-urlencode "git_repo=git@github.com:compucorp/ies.git" '
            f'--data-urlencode "git_tag=agent/IES-123-fix" '
            f'"$JENKINS_URL{_CREATE_JOB_PATH}/buildWithParameters"'
        )
        assert analyze_run.extract_devsite_trigger_repo(cmd) == "ies"

    def test_extracts_repo_from_python_helper_call(self):
        cmd = (
            'python3 -c "from repro_helpers import trigger_dev_site; '
            'trigger_dev_site(git_repo=\\"git@github.com:compucorp/mm.git\\", '
            'git_tag=\\"agent/MM-1-fix\\", anondb_url=\\"x\\")"'
        )
        assert analyze_run.extract_devsite_trigger_repo(cmd) == "mm"

    def test_returns_none_when_no_repo_in_cmd(self):
        assert analyze_run.extract_devsite_trigger_repo("nothing here") is None

    def test_site_deployable_repos_includes_known_clients(self):
        """The list must include at least the canonical client sites the
        plan called out — guards against drift."""
        for r in ("ies", "mm", "ase", "cst", "civiplus-distribution"):
            assert r in analyze_run.SITE_DEPLOYABLE_REPOS, \
                f"{r!r} missing from SITE_DEPLOYABLE_REPOS"

    def test_site_deployable_repos_excludes_extensions_and_infra(self):
        """The audit must NOT silently accept a dev-site trigger for an
        extension or an infra repo.
        """
        for r in ("abn", "io.compuco.gocardless", "terraform", "jenkins",
                  "compuco.docker.images.php-fpm"):
            assert r not in analyze_run.SITE_DEPLOYABLE_REPOS, \
                f"{r!r} unexpectedly present in SITE_DEPLOYABLE_REPOS"


class TestBypassesAndEdgeCases:
    """Reviewer's second-round findings — bypass scenarios that must be closed."""

    def test_echo_containing_python_and_helper_call_not_allowed(self):
        """Reviewer-flagged: `echo "python3 -c '... trigger_dev_site('"`
        matches the previous regex `\\bpython3?\\b...\\btrigger_dev_site\\(`
        but is NOT a real Python execution — `echo` is the actual command."""
        cmds = [_bc(
            'echo "python3 -c \\"from repro_helpers import trigger_dev_site;'
            ' trigger_dev_site(g=\\"x\\")\\""',
            idx=1,
        )]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result == {"allowed": [], "disallowed": []}

    def test_heredoc_containing_helper_call_not_allowed(self):
        """`cat <<EOF\\npython3 ...trigger_dev_site(...\\nEOF` is writing text,
        not executing Python."""
        cmds = [_bc(
            "cat <<'EOF'\n"
            "python3 -c 'trigger_dev_site(g=\"x\")'\n"
            "EOF",
            idx=2,
        )]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result == {"allowed": [], "disallowed": []}

    def test_helper_call_with_non_allowlisted_repo_is_disallowed(self):
        """Reviewer-flagged BLOCKER: the SITE_DEPLOYABLE_REPOS cross-check
        must run INSIDE the classifier, not only in the post-loop reporter.
        Otherwise a helper call against terraform passes silently."""
        cmds = [_bc(
            'python3 -c "from repro_helpers import trigger_dev_site; '
            'trigger_dev_site(git_repo=\\"git@github.com:compucorp/terraform.git\\", '
            'git_tag=\\"agent/X-1-fix\\", anondb_url=\\"x\\")"',
            idx=3,
        )]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1, \
            "non-allowlisted repo must be disallowed regardless of detection path"

    def test_curl_to_devsite_job_with_non_allowlisted_repo_is_disallowed(self):
        """Same cross-check via the raw-curl path."""
        cmds = [_bc(
            'curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN '
            '--data-urlencode "git_repo=git@github.com:compucorp/terraform.git" '
            f'--data-urlencode "git_tag=agent/X-1-fix" '
            f'"$JENKINS_URL{_CREATE_JOB_PATH}/buildWithParameters"',
            idx=4,
        )]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1

    def test_helper_call_with_cd_prefix_still_allowed(self):
        """Legitimate `cd <workspace> && python3 -c "...trigger_dev_site(..."` —
        common idiom — must still be classified as allowed when the repo is OK."""
        cmds = [_bc(
            'cd ~/symphony_workspaces/IES-1 && python3 -c '
            '"from repro_helpers import trigger_dev_site; '
            'trigger_dev_site(git_repo=\\"git@github.com:compucorp/ies.git\\", '
            'git_tag=\\"agent/IES-1-fix\\", anondb_url=\\"x\\")"',
            idx=5,
        )]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1, result

    def test_extract_repo_without_dotgit_suffix(self):
        """`_GIT_REPO_RE` must not fall over when `.git` is missing — a
        urlencoded body or a sloppy invocation might omit it."""
        cmd = (
            'curl -X POST --data-urlencode '
            f'"git_repo=git@github.com:compucorp/ies&git_tag=foo" '
            f'"$JENKINS_URL{_CREATE_JOB_PATH}/buildWithParameters"'
        )
        assert analyze_run.extract_devsite_trigger_repo(cmd) == "ies"


class TestPollUntilDeployedAborted:
    def test_aborted_build_raises(self, monkeypatch):
        """A Jenkins build that gets aborted (Stop button) returns result=ABORTED.
        Must raise — not return a hostname. Reviewer-flagged second-round."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))
        import repro_helpers as rh

        monkeypatch.setenv("JENKINS_URL", "https://jenkins.test.local")
        monkeypatch.setenv("JENKINS_USER", "u")
        monkeypatch.setenv("JENKINS_TOKEN", "t")

        class _Resp:
            def __init__(self, **k):
                self._j = k.get("json_data")
                self.text = k.get("text", "")
                self.status_code = 200
            def raise_for_status(self): pass
            def json(self): return self._j

        responses = iter([
            _Resp(json_data={"executable": {"url": "https://j/b/1/"}}),
            _Resp(json_data={"result": "ABORTED", "building": False}),
        ])
        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        import pytest
        with pytest.raises(RuntimeError, match="ABORTED"):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=60)


class TestTriggerDevSiteLifespanDryRun:
    def test_lifespan_1_accepted_and_forwarded(self, monkeypatch):
        """Dry-run path: WORKFLOW.md passes `lifespan=(1 if dry_run else None)`.
        Confirm the helper accepts `lifespan=1` and forwards it as the string
        `"1"` (Jenkins job params are string-typed via buildWithParameters)."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))
        import repro_helpers as rh

        monkeypatch.setenv("JENKINS_URL", "https://jenkins.test.local")
        monkeypatch.setenv("JENKINS_USER", "u")
        monkeypatch.setenv("JENKINS_TOKEN", "t")
        captured = {}

        def fake_post(url, data, auth, timeout, headers=None):
            captured["data"] = data
            class _R:
                status_code = 201
                headers = {"Location": "https://j/q/1/"}
                def raise_for_status(self): pass
            return _R()

        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a",
                            lifespan=1)
        assert captured["data"]["lifespan"] == "1"


class TestReleaseJobDetection:
    """Tests for the Phase B (_Release Dev Site) path in detect_jenkins_writes."""

    def test_trigger_release_devsite_helper_call_is_allowed(self):
        """Calling `trigger_release_devsite(...)` from Python is allowed."""
        cmds = [
            _bc(
                'python3 -c "from repro_helpers import trigger_release_devsite; '
                'trigger_release_devsite(site_url=\\"host.cc-test.site\\", '
                'git_tag=\\"agent-IES-1-fix\\")"',
                idx=400,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1
        assert result["allowed"][0][0] == 400
        assert result["disallowed"] == []

    def test_curl_post_to_release_job_path_is_allowed(self):
        """A raw curl POST to the _Release Dev Site job path is allowed."""
        cmds = [
            _bc(
                'curl -sS -X POST -u "$JENKINS_USER:$JENKINS_TOKEN" '
                '"$JENKINS_URL/job/Deployments/job/'
                'Dev%20Sites%20-%20Compucontainer/job/_Release%20Dev%20Site'
                '/buildWithParameters" --data site_url=host.cc-test.site',
                idx=401,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 1
        assert result["disallowed"] == []

    def test_release_job_post_without_buildWithParameters_is_disallowed(self):
        """POST to the _Release Dev Site job but NOT to buildWithParameters is disallowed."""
        cmds = [
            _bc(
                'curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN '
                '"$JENKINS_URL/job/Deployments/job/'
                'Dev%20Sites%20-%20Compucontainer/job/_Release%20Dev%20Site/disable"',
                idx=402,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result["allowed"] == []
        assert len(result["disallowed"]) == 1

    def test_echo_containing_release_helper_is_not_allowed(self):
        """`echo "trigger_release_devsite("` is NOT a real Python execution."""
        cmds = [_bc('echo "trigger_release_devsite(site_url=x)"', idx=403)]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert result == {"allowed": [], "disallowed": []}

    def test_create_plus_release_both_allowed(self):
        """The full two-phase pattern: one Create trigger + one Release trigger,
        both classified as allowed."""
        cmds = [
            _bc(
                'python3 -c "from repro_helpers import trigger_dev_site; '
                'trigger_dev_site(git_repo=\\"git@github.com:compucorp/ies.git\\", '
                'git_tag=\\"before\\", anondb_url=\\"a\\")"',
                idx=10,
            ),
            _bc(
                'python3 -c "from repro_helpers import trigger_release_devsite; '
                'trigger_release_devsite(site_url=\\"host.cc-test.site\\", '
                'git_tag=\\"after\\")"',
                idx=20,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        assert len(result["allowed"]) == 2
        assert result["disallowed"] == []

    def test_three_allowed_triggers_would_still_be_classified_as_allowed(self):
        """detect_jenkins_writes classifies by kind; the COUNT check lives
        in the audit reporter, not in the classifier. Three triggers are all
        'allowed' by the classifier — the reporter emits the > 2 warning."""
        cmds = [
            _bc(
                'python3 -c "from repro_helpers import trigger_dev_site; '
                'trigger_dev_site(git_repo=\\"git@github.com:compucorp/ies.git\\", '
                'git_tag=\\"t1\\", anondb_url=\\"a\\")"',
                idx=1,
            ),
            _bc(
                'python3 -c "from repro_helpers import trigger_release_devsite; '
                'trigger_release_devsite(site_url=\\"h.cc-test.site\\", '
                'git_tag=\\"t2\\")"',
                idx=2,
            ),
            _bc(
                'python3 -c "from repro_helpers import trigger_dev_site; '
                'trigger_dev_site(git_repo=\\"git@github.com:compucorp/ies.git\\", '
                'git_tag=\\"t3\\", anondb_url=\\"a\\")"',
                idx=3,
            ),
        ]
        result = analyze_run.detect_jenkins_writes(cmds)
        # All three are classified allowed — count violation is the reporter's concern
        assert len(result["allowed"]) == 3
        assert result["disallowed"] == []

    def test_release_job_path_constant_is_defined(self):
        """Smoke test: JENKINS_RELEASE_JOB_PATH_SUBSTR is exported and
        contains the right substring."""
        assert "_Release%20Dev%20Site" in analyze_run.JENKINS_RELEASE_JOB_PATH_SUBSTR

    def test_allowed_job_paths_set_contains_both_paths(self):
        """_JENKINS_ALLOWED_JOB_PATHS must include both Create and Release paths."""
        paths = analyze_run._JENKINS_ALLOWED_JOB_PATHS
        assert analyze_run.JENKINS_DEVSITE_JOB_PATH_SUBSTR in paths
        assert analyze_run.JENKINS_RELEASE_JOB_PATH_SUBSTR in paths


class TestTriggerDevSiteLifespanNoneOmitted:
    def test_lifespan_none_does_not_post_lifespan_key(self, monkeypatch):
        """Reviewer-flagged: ensure lifespan=None (Jenkins default) omits the
        key entirely rather than posting `lifespan=None` or `lifespan=`."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))
        import repro_helpers as rh

        monkeypatch.setenv("JENKINS_URL", "https://jenkins.test.local")
        monkeypatch.setenv("JENKINS_USER", "u")
        monkeypatch.setenv("JENKINS_TOKEN", "t")
        captured = {}

        def fake_post(url, data, auth, timeout, headers=None):
            captured["data"] = data
            class _R:
                status_code = 201
                headers = {"Location": "https://j/q/1/"}
                def raise_for_status(self): pass
            return _R()

        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a")
        assert "lifespan" not in captured["data"], \
            f"lifespan key should be omitted when None; got {captured['data']!r}"
