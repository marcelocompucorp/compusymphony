"""Unit tests for the Jenkins dev-site provisioning helpers in repro_helpers.py.

Covers:
- trigger_dev_site(): POST /buildWithParameters with correct params, returns queue URL
- poll_until_deployed(): walks queue → build → console, returns hostname
- _extract_devsite_hostname(): regex extraction from Pipeline-Mysql8 console line
- resolve_anondb_url(): Mongo prod-hostname lookup + anondbs probe
- wait_until_site_up(): HTTP readiness poll after Jenkins SUCCESS
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "prompts"))

import pytest

import repro_helpers as rh


# ---------- Fixtures ----------

@pytest.fixture
def jenkins_env(monkeypatch):
    """Set fake Jenkins env vars for test isolation."""
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.test.local")
    monkeypatch.setenv("JENKINS_USER", "openclawautomation")
    monkeypatch.setenv("JENKINS_TOKEN", "fake_jenkins_token")


class _FakeResponse:
    def __init__(self, *, status=200, json_data=None, text="", headers=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._json is not None:
            return self._json
        import json as _json
        return _json.loads(self.text)


# ---------- _extract_devsite_hostname ----------

class TestExtractDevsiteHostname:
    def test_extracts_from_public_pipeline_mysql8_line(self):
        """Real console line from a successful public-site build."""
        console = (
            "...lots of build output...\n"
            "Deployments » Dev Sites - Compucontainer » Pipeline-Mysql8 "
            "#1237-uniformlycreativegrizzly.public.cc-test.site completed. "
            "Result was SUCCESS\n"
            "+ echo Please visit Grafana ...\n"
        )
        assert rh._extract_devsite_hostname(console) == \
            "uniformlycreativegrizzly.public.cc-test.site"

    def test_returns_none_when_no_match(self):
        """Console with no Pipeline-Mysql8 completion line → None."""
        assert rh._extract_devsite_hostname("nothing relevant here") is None
        assert rh._extract_devsite_hostname("") is None

    def test_extracts_from_internal_pattern_if_present(self):
        """Internal (non-public) sites: same pattern minus .public segment."""
        console = (
            "Deployments » Dev Sites - Compucontainer » Pipeline-Mysql8 "
            "#998-quietfoggypanda.cc-test.site completed. Result was SUCCESS\n"
        )
        host = rh._extract_devsite_hostname(console)
        # Must accept the internal pattern too (no `.public.`)
        assert host == "quietfoggypanda.cc-test.site"

    def test_extracts_docker_cc_test_site_pattern(self):
        """Jenkins internal builds sometimes emit *.docker.cc-test.site
        (observed in IESBUILD-242 dry-run builds #1257–#1259). The regex
        must match this subdomain shape as well."""
        console = (
            "Deployments » Dev Sites - Compucontainer » Pipeline-Mysql8 "
            "#1259-especiallyadequatefoal.docker.cc-test.site completed. "
            "Result was SUCCESS\n"
        )
        assert rh._extract_devsite_hostname(console) == \
            "especiallyadequatefoal.docker.cc-test.site"

    def test_ignores_failed_builds(self):
        """A 'completed. Result was FAILURE' line should NOT yield a hostname."""
        console = (
            "Pipeline-Mysql8 #5-failednastylizard.public.cc-test.site completed. "
            "Result was FAILURE\n"
        )
        assert rh._extract_devsite_hostname(console) is None


# ---------- trigger_dev_site ----------

class TestTriggerDevSite:
    def test_posts_to_buildWithParameters_with_required_params(
        self, jenkins_env, monkeypatch
    ):
        """trigger_dev_site builds the canonical POST URL and includes mandatory params."""
        captured = {}

        def fake_post(url, data, auth, timeout, headers=None):
            captured["url"] = url
            captured["data"] = data
            captured["auth"] = auth
            captured["timeout"] = timeout
            return _FakeResponse(status=201, headers={
                "Location": "https://jenkins.test.local/queue/item/42/"
            })

        monkeypatch.setattr(rh.requests, "post", fake_post)

        queue_url = rh.trigger_dev_site(
            git_repo="git@github.com:compucorp/ies.git",
            git_tag="agent/IES-123-fix",
            anondb_url="https://anondbs.cc-infra.tools/dir.php?name=ies_staging_2024",
        )

        assert queue_url == "https://jenkins.test.local/queue/item/42/"
        assert captured["url"] == (
            "https://jenkins.test.local/job/Deployments/job/"
            "Dev%20Sites%20-%20Compucontainer/job/"
            "Create%20Dev%20Site%20-%20Client%20Specific/buildWithParameters"
        )
        assert captured["auth"] == ("openclawautomation", "fake_jenkins_token")
        assert captured["data"]["git_repo"] == "git@github.com:compucorp/ies.git"
        assert captured["data"]["git_tag"] == "agent/IES-123-fix"
        assert captured["data"]["anonymised_database_url"] == \
            "https://anondbs.cc-infra.tools/dir.php?name=ies_staging_2024"
        # Sensible defaults
        assert captured["data"]["public_site"] == "false"
        assert captured["data"]["MAUTIC_ENABLED"] == "false"
        assert captured["data"]["REDIS_ENABLED"] == "false"

    def test_lifespan_param_forwarded_when_given(self, jenkins_env, monkeypatch):
        captured = {}

        def fake_post(url, data, auth, timeout, headers=None):
            captured["data"] = data
            return _FakeResponse(status=201, headers={"Location": "https://j/q/1/"})

        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_dev_site(
            git_repo="g", git_tag="t",
            anondb_url="a", lifespan=1,
        )
        assert captured["data"]["lifespan"] == "1"

    def test_public_site_requires_client_name(self, jenkins_env, monkeypatch):
        """public_site=True without client_name must raise — Jenkins job requires it."""
        with pytest.raises(ValueError, match="client_name"):
            rh.trigger_dev_site(
                git_repo="g", git_tag="t", anondb_url="a",
                public=True,
            )

    def test_public_site_with_client_name_sets_flags(
        self, jenkins_env, monkeypatch
    ):
        captured = {}

        def fake_post(url, data, auth, timeout, headers=None):
            captured["data"] = data
            return _FakeResponse(status=201, headers={"Location": "https://j/q/2/"})

        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_dev_site(
            git_repo="g", git_tag="t", anondb_url="a",
            public=True, client_name="IES",
        )
        assert captured["data"]["public_site"] == "true"
        assert captured["data"]["client_name"] == "IES"

    def test_raises_on_http_error(self, jenkins_env, monkeypatch):
        def fake_post(url, data, auth, timeout, headers=None):
            return _FakeResponse(status=403)

        monkeypatch.setattr(rh.requests, "post", fake_post)
        import requests
        with pytest.raises(requests.HTTPError):
            rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a")

    def test_raises_on_missing_location_header(self, jenkins_env, monkeypatch):
        """Jenkins normally returns 201 with Location: <queue-url>; missing == anomaly."""
        def fake_post(url, data, auth, timeout, headers=None):
            return _FakeResponse(status=201, headers={})

        monkeypatch.setattr(rh.requests, "post", fake_post)
        with pytest.raises(RuntimeError, match="Location"):
            rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a")


# ---------- poll_until_deployed ----------

class TestPollUntilDeployed:
    def test_returns_hostname_on_successful_deploy(self, jenkins_env, monkeypatch):
        """Queue → executable → SUCCESS → hostname from console."""
        # Sequence of GET responses
        responses = iter([
            # Queue item: not yet executable
            _FakeResponse(json_data={"why": "In the quiet zone"}),
            # Queue item: now has executable
            _FakeResponse(json_data={
                "executable": {"url": "https://jenkins.test.local/job/.../1254/"}
            }),
            # Build: still running
            _FakeResponse(json_data={"result": None, "building": True}),
            # Build: SUCCESS
            _FakeResponse(json_data={"result": "SUCCESS", "building": False}),
            # Console fetch
            _FakeResponse(text=(
                "Deployments » Dev Sites - Compucontainer » Pipeline-Mysql8 "
                "#1254-happyfunlemur.public.cc-test.site completed. "
                "Result was SUCCESS\n"
            )),
        ])

        def fake_get(url, auth, timeout):
            return next(responses)

        monkeypatch.setattr(rh.requests, "get", fake_get)
        # Patch sleep so the test is instant
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        host = rh.poll_until_deployed(
            "https://jenkins.test.local/queue/item/42/",
            timeout_s=60,
        )
        assert host == "happyfunlemur.public.cc-test.site"

    def test_raises_on_build_failure(self, jenkins_env, monkeypatch):
        """If the build completes with result=FAILURE, raise without returning."""
        responses = iter([
            _FakeResponse(json_data={
                "executable": {"url": "https://jenkins.test.local/job/.../99/"}
            }),
            _FakeResponse(json_data={"result": "FAILURE", "building": False}),
        ])

        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="FAILURE"):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=60)

    def test_raises_on_timeout(self, jenkins_env, monkeypatch):
        """If the build doesn't complete within timeout_s, raise TimeoutError."""
        # Always-pending queue
        def fake_get(url, auth, timeout):
            return _FakeResponse(json_data={"why": "still queued"})

        monkeypatch.setattr(rh.requests, "get", fake_get)
        # Make sleep advance our fake clock so the timeout actually fires
        fake_now = [0.0]

        def fake_sleep(s):
            fake_now[0] += s

        monkeypatch.setattr(rh.time, "sleep", fake_sleep)
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])

        with pytest.raises(TimeoutError):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=5)

    def test_raises_when_hostname_not_found_in_console(
        self, jenkins_env, monkeypatch
    ):
        """Build SUCCESS but the Pipeline-Mysql8 hostname line is absent → error."""
        responses = iter([
            _FakeResponse(json_data={
                "executable": {"url": "https://jenkins.test.local/job/.../99/"}
            }),
            _FakeResponse(json_data={"result": "SUCCESS", "building": False}),
            _FakeResponse(text="no relevant line here\n"),
        ])

        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="hostname"):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=60)

    def test_raises_on_public_host_mismatch_when_expecting_internal(
        self, jenkins_env, monkeypatch
    ):
        """If trigger_dev_site was called with public=False but Jenkins returns
        a `.public.cc-test.site` hostname, that's a Jenkins-side inconsistency
        — raise rather than silently accept (which would route the agent to
        a wrong-auth site)."""
        responses = iter([
            _FakeResponse(json_data={
                "executable": {"url": "https://jenkins.test.local/job/.../99/"}
            }),
            _FakeResponse(json_data={"result": "SUCCESS", "building": False}),
            _FakeResponse(text=(
                "Pipeline-Mysql8 #99-foobar.public.cc-test.site completed. "
                "Result was SUCCESS\n"
            )),
        ])
        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="public"):
            rh.poll_until_deployed("https://j/q/1/", expect_public=False,
                                   timeout_s=60)

    def test_internal_host_passes_expect_public_false(
        self, jenkins_env, monkeypatch
    ):
        """And the converse: an internal hostname matches expect_public=False."""
        responses = iter([
            _FakeResponse(json_data={
                "executable": {"url": "https://jenkins.test.local/job/.../1/"}
            }),
            _FakeResponse(json_data={"result": "SUCCESS", "building": False}),
            _FakeResponse(text=(
                "Pipeline-Mysql8 #1-internalhost.cc-test.site completed. "
                "Result was SUCCESS\n"
            )),
        ])
        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)
        host = rh.poll_until_deployed("https://j/q/1/", expect_public=False,
                                      timeout_s=60)
        assert host == "internalhost.cc-test.site"

    def test_raises_fast_on_cancelled_queue_item(self, jenkins_env, monkeypatch):
        """A cancelled queue item never produces `executable` — fail fast, not at timeout.

        Reviewer flagged BLOCKER: without this, the function waits the full
        timeout_s on a cancelled item, wasting up to 15min per affected run.
        """
        get_calls = [0]

        def fake_get(url, auth, timeout):
            get_calls[0] += 1
            return _FakeResponse(json_data={"cancelled": True, "why": "Cancelled by user"})

        monkeypatch.setattr(rh.requests, "get", fake_get)
        # Advance fake clock on sleep so even if the early-raise is missing,
        # the test bounds out instead of hanging on real wall time.
        fake_now = [0.0]

        def fake_sleep(s):
            fake_now[0] += s

        monkeypatch.setattr(rh.time, "sleep", fake_sleep)
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])

        with pytest.raises(RuntimeError, match="cancel"):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=900)

        # Belt-and-braces: must fail fast (1 call), not poll the full deadline
        assert get_calls[0] <= 2, \
            f"cancellation should be detected on first poll, not after {get_calls[0]}"


class TestExtractDevsiteHostnamePicksLastSuccess:
    def test_returns_last_success_when_multiple_lines_present(self):
        """Reviewer flagged: a flaky sub-build that emits SUCCESS-then-retry
        could leave multiple `Pipeline-Mysql8 #N-host completed. Result was
        SUCCESS` lines in the console. The final one is authoritative.
        """
        console = (
            "...\n"
            "Pipeline-Mysql8 #1-stalehost.public.cc-test.site completed. "
            "Result was SUCCESS\n"
            "(some retry happened)\n"
            "Pipeline-Mysql8 #2-realhost.public.cc-test.site completed. "
            "Result was SUCCESS\n"
        )
        assert rh._extract_devsite_hostname(console) == \
            "realhost.public.cc-test.site"


class TestTriggerDevSiteValidation:
    """Reviewer-flagged WARNINGs: empty inputs + out-of-range lifespan."""

    def test_raises_on_empty_git_repo(self, jenkins_env):
        with pytest.raises(ValueError, match="git_repo"):
            rh.trigger_dev_site(git_repo="", git_tag="t", anondb_url="a")

    def test_raises_on_empty_git_tag(self, jenkins_env):
        with pytest.raises(ValueError, match="git_tag"):
            rh.trigger_dev_site(git_repo="g", git_tag="", anondb_url="a")

    def test_raises_on_empty_anondb_url(self, jenkins_env):
        with pytest.raises(ValueError, match="anondb"):
            rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="")

    def test_raises_on_lifespan_out_of_range_internal(self, jenkins_env):
        """Internal lifespan range is 1–31. 32 must raise."""
        with pytest.raises(ValueError, match="lifespan"):
            rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a",
                                lifespan=32)

    def test_raises_on_lifespan_zero(self, jenkins_env):
        with pytest.raises(ValueError, match="lifespan"):
            rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a",
                                lifespan=0)

    def test_lifespan_90_allowed_for_public(self, jenkins_env, monkeypatch):
        """Public lifespan range is 1–90. 90 must be accepted."""
        def fake_post(url, data, auth, timeout, headers=None):
            return _FakeResponse(status=201, headers={"Location": "https://j/q/1/"})
        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_dev_site(
            git_repo="g", git_tag="t", anondb_url="a",
            public=True, client_name="X", lifespan=90,
        )

    def test_lifespan_91_rejected_even_for_public(self, jenkins_env):
        with pytest.raises(ValueError, match="lifespan"):
            rh.trigger_dev_site(
                git_repo="g", git_tag="t", anondb_url="a",
                public=True, client_name="X", lifespan=91,
            )

    def test_mautic_param_forwarded(self, jenkins_env, monkeypatch):
        """For email-template tickets the agent can opt-in to Mautic on the
        dev site via mautic=True. Default remains False — most tickets don't
        need it and enabling it adds ~30s build time."""
        captured = {}
        def fake_post(url, data, auth, timeout, headers=None):
            captured["data"] = data
            return _FakeResponse(status=201, headers={"Location": "https://j/q/1/"})
        monkeypatch.setattr(rh.requests, "post", fake_post)

        rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a")
        assert captured["data"]["MAUTIC_ENABLED"] == "false"

        rh.trigger_dev_site(git_repo="g", git_tag="t", anondb_url="a",
                            mautic=True)
        assert captured["data"]["MAUTIC_ENABLED"] == "true"


# ---------- devsite_git_tag ----------

class TestDevsiteGitTag:
    """devsite_git_tag(branch) returns a Docker-safe equivalent of the branch name.

    Docker tags cannot contain '/'. Agent branches use 'agent/<TICKET>-fix'.
    The safe tag is used as git_tag in trigger_dev_site; a lightweight git tag
    is pushed to remote before the Jenkins trigger and deleted after.
    """

    def test_replaces_slash_with_dash(self):
        assert rh.devsite_git_tag("agent/IESBUILD-242-fix") == "agent-IESBUILD-242-fix"

    def test_multiple_slashes(self):
        assert rh.devsite_git_tag("feat/scope/thing") == "feat-scope-thing"

    def test_already_safe_unchanged(self):
        assert rh.devsite_git_tag("already-safe") == "already-safe"

    def test_preserves_case(self):
        assert rh.devsite_git_tag("Agent/IES-123-Fix") == "Agent-IES-123-Fix"

    def test_empty_string_returns_empty(self):
        assert rh.devsite_git_tag("") == ""


# ---------- resolve_anondb_url ----------

@pytest.fixture
def mongo_env(monkeypatch):
    """Set fake Mongo env vars so resolve_anondb_url can build a URI."""
    monkeypatch.setenv("MONGO_USER", "testuser")
    monkeypatch.setenv("MONGO_PASSWORD", "testpass")
    monkeypatch.setenv("MONGO_HOST", "localhost")
    monkeypatch.setenv("MONGO_PORT", "27017")
    monkeypatch.setenv("MONGO_AUTH_SOURCE", "admin")


class TestResolveAnondbUrl:
    """resolve_anondb_url(staging_hostname) → best anondb URL for a Jenkins
    dev-site trigger.

    Strategy:
      1. Query Mongo for sites with the same `repository` as staging_hostname.
      2. Filter to production hostnames (exclude *.cc-staging.site,
         *.cc-test.site, *.cc-data.site, *.cc-prelive.site).
      3. For each candidate, probe `anondbs.cc-infra.tools/dir.php?name=<host>&api=1`;
         the first that returns a non-empty JSON array is the winner.
      4. If no prod hostname resolves, fall back to staging_hostname itself.
      5. If the staging hostname also fails to probe, return None (caller skips
         the dev-site step).
    """

    def _mongo_mock(self, monkeypatch, sites_by_repo):
        """Patch rh.MongoClient so find() returns `sites_by_repo` documents."""
        class _FakeCursor:
            def __init__(self, docs): self._docs = docs
            def __iter__(self): return iter(self._docs)

        class _FakeCollection:
            def __init__(self, docs): self._docs = docs
            def find(self, q, proj=None): return _FakeCursor(self._docs)
            def find_one(self, q, proj=None):
                return self._docs[0] if self._docs else None

        class _FakeDB:
            def __init__(self, docs): self.sites = _FakeCollection(docs)
            def __getitem__(self, name): return _FakeCollection([])

        class _FakeClient:
            def __init__(self, docs): self._db = _FakeDB(docs)
            def __getitem__(self, name): return self._db
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(rh, "MongoClient", lambda *a, **kw: _FakeClient(sites_by_repo))

    def test_prefers_staging_when_it_has_an_anondbs_entry(self, mongo_env, monkeypatch):
        """Staging hostname has an anondbs entry → return it immediately, no Mongo needed."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])

        def fake_get(url, timeout):
            # Both staging and prod have entries; staging should win.
            return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')

        monkeypatch.setattr(rh.requests, "get", fake_get)
        result = rh.resolve_anondb_url("ies2.cc-staging.site")
        assert result == "https://anondbs.cc-infra.tools/dir.php?name=ies2.cc-staging.site"

    def test_falls_back_to_prod_when_staging_has_no_anondbs_entry(self, mongo_env, monkeypatch):
        """Staging probe returns empty → fall back to production sibling."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])

        def fake_get(url, timeout):
            if "www.the-ies.org" in url:
                return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')
            return _FakeResponse(status=200, text='[]')

        monkeypatch.setattr(rh.requests, "get", fake_get)
        result = rh.resolve_anondb_url("ies2.cc-staging.site")
        assert result == "https://anondbs.cc-infra.tools/dir.php?name=www.the-ies.org"

    def test_non_prod_siblings_never_probed_as_candidates(self, mongo_env, monkeypatch):
        """cc-data / cc-prelive / cc-test siblings are excluded from prod candidates.

        The input staging hostname (ies2.cc-staging.site) IS probed in step 1.
        Only the production sibling (www.the-ies.org) should be tried as a
        prod candidate; the other non-prod siblings must never be candidates.
        """
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "ies2.cc-data.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "ies.cc-prelive.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "foo.cc-test.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])
        probed = []
        def fake_get(url, timeout):
            probed.append(url)
            if "www.the-ies.org" in url:
                return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')
            return _FakeResponse(status=200, text='[]')
        monkeypatch.setattr(rh.requests, "get", fake_get)
        result = rh.resolve_anondb_url("ies2.cc-staging.site")
        assert result == "https://anondbs.cc-infra.tools/dir.php?name=www.the-ies.org"
        # The input staging hostname may be probed (step 1), but the other
        # non-prod siblings (cc-data, cc-prelive, cc-test) must never appear.
        for url in probed:
            for bad in ("cc-data", "cc-prelive", "cc-test"):
                assert bad not in url, f"non-prod sibling probed as candidate: {url}"

    def test_returns_none_when_staging_has_no_entry_and_no_prod_probe_succeeds(
        self, mongo_env, monkeypatch
    ):
        """Staging returns empty and prod returns empty → None."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])
        monkeypatch.setattr(rh.requests, "get",
                            lambda url, timeout: _FakeResponse(status=200, text='[]'))
        assert rh.resolve_anondb_url("ies2.cc-staging.site") is None

    def test_returns_none_when_nothing_resolves(self, mongo_env, monkeypatch):
        """All probes return empty — caller should skip dev-site step."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])
        monkeypatch.setattr(rh.requests, "get",
                            lambda url, timeout: _FakeResponse(status=200, text='[]'))
        assert rh.resolve_anondb_url("ies2.cc-staging.site") is None

    def test_returns_none_when_staging_hostname_not_in_mongo(self, mongo_env, monkeypatch):
        """anondbs probe returns empty AND Mongo has no doc → None."""
        self._mongo_mock(monkeypatch, [])
        monkeypatch.setattr(rh.requests, "get",
                            lambda url, timeout: _FakeResponse(status=200, text='[]'))
        assert rh.resolve_anondb_url("unknown.cc-staging.site") is None

    def test_probe_http_error_is_skipped_not_raised(self, mongo_env, monkeypatch):
        """A network error probing one candidate skips it; other candidates still tried."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "other-prod.example.com", "repository": "git@github.com:compucorp/ies.git"},
        ])
        import requests as req_mod
        def fake_get(url, timeout):
            if "www.the-ies.org" in url:
                raise req_mod.RequestException("network error")
            if "other-prod.example.com" in url:
                return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')
            return _FakeResponse(status=200, text='[]')
        monkeypatch.setattr(rh.requests, "get", fake_get)
        result = rh.resolve_anondb_url("ies2.cc-staging.site")
        assert result == "https://anondbs.cc-infra.tools/dir.php?name=other-prod.example.com"

    def test_multiple_prod_hostnames_returns_first_that_resolves(self, mongo_env, monkeypatch):
        """With two prod hostnames, the first one with a valid probe wins."""
        self._mongo_mock(monkeypatch, [
            {"_id": "ies2.cc-staging.site", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
            {"_id": "www2.the-ies.org", "repository": "git@github.com:compucorp/ies.git"},
        ])
        def fake_get(url, timeout):
            if "www.the-ies.org" in url:
                return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')
            if "www2.the-ies.org" in url:
                return _FakeResponse(status=200, text='[{"name":"drupal.sql.gz"}]')
            return _FakeResponse(status=200, text='[]')
        monkeypatch.setattr(rh.requests, "get", fake_get)
        result = rh.resolve_anondb_url("ies2.cc-staging.site")
        # First prod hostname wins
        assert result == "https://anondbs.cc-infra.tools/dir.php?name=www.the-ies.org"


# ---------- poll_until_deployed — raise_on_timeout=False (stall-detector fix) ----------

class TestPollUntilDeployedPartialTimeout:
    """poll_until_deployed(..., raise_on_timeout=False) returns None on timeout
    instead of raising TimeoutError.

    This enables the WORKFLOW.md two-phase pattern: the agent calls
    poll_until_deployed in short bursts (timeout_s=90) to keep Claude API
    activity flowing.  If the build isn't done yet it gets None, outputs a
    status line, and re-invokes the script — preventing the stall-detector
    from firing.
    """

    def test_returns_none_on_timeout_when_not_raising(self, jenkins_env, monkeypatch):
        """Always-pending queue + raise_on_timeout=False → returns None (not raises)."""
        def fake_get(url, auth, timeout):
            return _FakeResponse(json_data={"why": "still queued"})

        fake_now = [0.0]

        def fake_sleep(s):
            fake_now[0] += s

        monkeypatch.setattr(rh.requests, "get", fake_get)
        monkeypatch.setattr(rh.time, "sleep", fake_sleep)
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])

        result = rh.poll_until_deployed(
            "https://j/q/1/", timeout_s=5, raise_on_timeout=False
        )
        assert result is None

    def test_still_raises_on_timeout_by_default(self, jenkins_env, monkeypatch):
        """Default behaviour (raise_on_timeout=True) unchanged."""
        def fake_get(url, auth, timeout):
            return _FakeResponse(json_data={"why": "still queued"})

        fake_now = [0.0]
        monkeypatch.setattr(rh.requests, "get", fake_get)
        monkeypatch.setattr(rh.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])

        with pytest.raises(TimeoutError):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=5)

    def test_returns_none_when_build_not_complete_in_time(self, jenkins_env, monkeypatch):
        """Queue item assigned but build still running when timeout hits → None."""
        fake_now = [0.0]

        def fake_get(url, auth, timeout):
            # Queue has executable, but build never finishes
            if "queue" in url:
                return _FakeResponse(json_data={
                    "executable": {"url": "https://j/job/1/"}
                })
            return _FakeResponse(json_data={"result": None, "building": True})

        monkeypatch.setattr(rh.requests, "get", fake_get)
        monkeypatch.setattr(rh.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])

        result = rh.poll_until_deployed("https://j/q/1/", timeout_s=5,
                                        raise_on_timeout=False)
        assert result is None

    def test_still_raises_on_build_failure_regardless_of_flag(
        self, jenkins_env, monkeypatch
    ):
        """raise_on_timeout=False only affects TimeoutError; FAILURE still raises."""
        responses = iter([
            _FakeResponse(json_data={
                "executable": {"url": "https://j/job/99/"}
            }),
            _FakeResponse(json_data={"result": "FAILURE", "building": False}),
        ])
        monkeypatch.setattr(rh.requests, "get", lambda *a, **k: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="FAILURE"):
            rh.poll_until_deployed("https://j/q/1/", timeout_s=60,
                                   raise_on_timeout=False)


# ---------- wait_until_site_up ----------

class TestWaitUntilSiteUp:
    """wait_until_site_up(hostname) polls https://<hostname>/ until HTTP 200.

    Dev sites use compucorp_admin/compucorp_admin basic auth. The function
    uses requests.get with HTTP Basic Auth and follows redirects.
    """

    def test_returns_immediately_when_site_already_up(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "get",
                            lambda url, auth, timeout, allow_redirects: _FakeResponse(status=200))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)
        # Should not raise
        rh.wait_until_site_up("happylemur.cc-test.site", timeout_s=60)

    def test_retries_on_503_then_succeeds(self, jenkins_env, monkeypatch):
        """Site returns 503 twice (containers starting), then 200."""
        responses = iter([
            _FakeResponse(status=503),
            _FakeResponse(status=503),
            _FakeResponse(status=200),
        ])
        monkeypatch.setattr(rh.requests, "get",
                            lambda *a, **kw: next(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)
        rh.wait_until_site_up("happylemur.cc-test.site", timeout_s=60)

    def test_raises_timeout_when_site_never_up(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "get",
                            lambda *a, **kw: _FakeResponse(status=503))
        fake_now = [0.0]
        monkeypatch.setattr(rh.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])
        with pytest.raises(TimeoutError, match="site"):
            rh.wait_until_site_up("happylemur.cc-test.site", timeout_s=30)

    def test_network_error_retried_not_raised(self, jenkins_env, monkeypatch):
        """Connection refused / timeout from requests is retried, not propagated."""
        import requests as req_mod
        responses = iter([
            req_mod.ConnectionError("refused"),
            req_mod.ConnectionError("refused"),
            _FakeResponse(status=200),
        ])
        def fake_get(*a, **kw):
            r = next(responses)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(rh.requests, "get", fake_get)
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)
        rh.wait_until_site_up("happylemur.cc-test.site", timeout_s=60)

    def test_uses_correct_url_and_basic_auth(self, jenkins_env, monkeypatch):
        """Polls https://<hostname>/ with compucorp_admin credentials."""
        captured = {}
        def fake_get(url, auth, timeout, allow_redirects):
            captured["url"] = url
            captured["auth"] = auth
            return _FakeResponse(status=200)
        monkeypatch.setattr(rh.requests, "get", fake_get)
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)
        rh.wait_until_site_up("testhost.cc-test.site", timeout_s=60)
        assert captured["url"] == "https://testhost.cc-test.site/"
        assert captured["auth"] == ("compucorp_admin", "compucorp_admin")


# ---------- trigger_release_devsite ----------

class TestTriggerReleaseDevsite:
    """trigger_release_devsite(site_url, git_tag) POSTs to the _Release Dev Site job.

    Parameters:
      - site_url: bare hostname of an existing dev site
      - git_tag: agent fix branch tag (Docker-safe)
    anonymised_database_url is always empty (skip DB reimport — preserve Phase A data).
    Returns the queue-item URL from the Location header.
    """

    def test_posts_to_correct_job_url(self, jenkins_env, monkeypatch):
        """POSTs to _RELEASE_JOB_PATH/buildWithParameters on JENKINS_URL."""
        captured = {}

        def fake_post(url, data, auth, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["auth"] = auth
            return _FakeResponse(status=201, headers={"Location": "https://jenkins.test.local/queue/item/99/"})

        monkeypatch.setattr(rh.requests, "post", fake_post)

        queue_url = rh.trigger_release_devsite(
            site_url="gentlylivinggazelle.docker.cc-test.site",
            git_tag="agent-IESBUILD-123-fix",
        )

        assert queue_url == "https://jenkins.test.local/queue/item/99/"
        assert "_Release%20Dev%20Site/buildWithParameters" in captured["url"]
        assert captured["auth"] == ("openclawautomation", "fake_jenkins_token")

    def test_passes_correct_params(self, jenkins_env, monkeypatch):
        """site_url and git_tag forwarded; anonymised_database_url is empty string."""
        captured = {}

        def fake_post(url, data, auth, timeout):
            captured["data"] = data
            return _FakeResponse(status=201, headers={"Location": "https://j/q/1/"})

        monkeypatch.setattr(rh.requests, "post", fake_post)
        rh.trigger_release_devsite(
            site_url="gentlylivinggazelle.docker.cc-test.site",
            git_tag="agent-IESBUILD-123-fix",
        )

        assert captured["data"]["site_url"] == "gentlylivinggazelle.docker.cc-test.site"
        assert captured["data"]["git_tag"] == "agent-IESBUILD-123-fix"
        assert captured["data"]["anonymised_database_url"] == ""

    def test_raises_on_empty_site_url(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "post", lambda *a, **kw: None)
        with pytest.raises(ValueError, match="site_url"):
            rh.trigger_release_devsite(site_url="", git_tag="agent-IESBUILD-123-fix")

    def test_raises_on_non_cctest_site_url(self, jenkins_env, monkeypatch):
        """site_url must be a *.cc-test.site hostname — passing a production or staging
        hostname would silently release to the wrong target."""
        monkeypatch.setattr(rh.requests, "post", lambda *a, **kw: None)
        with pytest.raises(ValueError, match=r"cc-test\.site"):
            rh.trigger_release_devsite(
                site_url="ies2.cc-staging.site",  # staging host, not a dev site
                git_tag="agent-IES-1-fix",
            )

    def test_raises_on_full_url_site_url(self, jenkins_env, monkeypatch):
        """site_url must be a bare hostname (no https:// prefix)."""
        monkeypatch.setattr(rh.requests, "post", lambda *a, **kw: None)
        with pytest.raises(ValueError, match=r"cc-test\.site"):
            rh.trigger_release_devsite(
                site_url="https://gentlylivinggazelle.docker.cc-test.site",
                git_tag="agent-IES-1-fix",
            )

    def test_raises_on_empty_git_tag(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "post", lambda *a, **kw: None)
        with pytest.raises(ValueError, match="git_tag"):
            rh.trigger_release_devsite(
                site_url="gentlylivinggazelle.docker.cc-test.site",
                git_tag="",
            )

    def test_raises_on_http_error(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "post",
                            lambda *a, **kw: _FakeResponse(status=403))
        import requests
        with pytest.raises(requests.HTTPError):
            rh.trigger_release_devsite(
                site_url="gentlylivinggazelle.docker.cc-test.site",
                git_tag="t",
            )

    def test_raises_on_missing_location_header(self, jenkins_env, monkeypatch):
        monkeypatch.setattr(rh.requests, "post",
                            lambda *a, **kw: _FakeResponse(status=201, headers={}))
        with pytest.raises(RuntimeError, match="Location"):
            rh.trigger_release_devsite(
                site_url="gentlylivinggazelle.docker.cc-test.site",
                git_tag="t",
            )


# ---------- poll_until_released ----------

class TestPollUntilReleased:
    """poll_until_released(queue_url) waits for the _Release Dev Site build to complete.

    Returns site_url (the hostname already known by the caller) on SUCCESS.
    Same queue → build two-phase pattern as poll_until_deployed.
    No console-text hostname extraction needed.
    """

    _QUEUE_URL = "https://jenkins.test.local/queue/item/99/"
    _BUILD_URL = "https://jenkins.test.local/job/x/42/"
    _HOST = "gentlylivinggazelle.docker.cc-test.site"

    def _make_get(self, responses):
        it = iter(responses)
        def fake_get(url, auth, timeout):
            return next(it)
        return fake_get

    def test_returns_site_url_on_success(self, jenkins_env, monkeypatch):
        """Returns the site_url string on a clean SUCCESS build."""
        responses = [
            # Queue resolves immediately
            _FakeResponse(json_data={"executable": {"url": self._BUILD_URL}, "cancelled": False}),
            # Build completes SUCCESS
            _FakeResponse(json_data={"result": "SUCCESS"}),
        ]
        monkeypatch.setattr(rh.requests, "get", self._make_get(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        result = rh.poll_until_released(
            self._QUEUE_URL,
            site_url=self._HOST,
            timeout_s=300,
        )
        assert result == self._HOST

    def test_raises_on_failure_result(self, jenkins_env, monkeypatch):
        """RuntimeError when build result is FAILURE."""
        responses = [
            _FakeResponse(json_data={"executable": {"url": self._BUILD_URL}, "cancelled": False}),
            _FakeResponse(json_data={"result": "FAILURE"}),
        ]
        monkeypatch.setattr(rh.requests, "get", self._make_get(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="FAILURE"):
            rh.poll_until_released(self._QUEUE_URL, site_url=self._HOST, timeout_s=300)

    def test_raises_on_cancelled_queue_item(self, jenkins_env, monkeypatch):
        """RuntimeError when Jenkins cancels the queue item."""
        responses = [
            _FakeResponse(json_data={"cancelled": True, "why": "User cancelled"}),
        ]
        monkeypatch.setattr(rh.requests, "get", self._make_get(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        with pytest.raises(RuntimeError, match="cancelled"):
            rh.poll_until_released(self._QUEUE_URL, site_url=self._HOST, timeout_s=300)

    def test_returns_none_on_timeout_when_raise_on_timeout_false(
        self, jenkins_env, monkeypatch
    ):
        """Returns None (not TimeoutError) when raise_on_timeout=False and deadline hits."""
        fake_now = [0.0]
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])
        monkeypatch.setattr(rh.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))

        def fake_get(url, auth, timeout):
            fake_now[0] += 200  # advance past deadline on first poll
            return _FakeResponse(json_data={"executable": None, "cancelled": False})

        monkeypatch.setattr(rh.requests, "get", fake_get)

        result = rh.poll_until_released(
            self._QUEUE_URL, site_url=self._HOST, timeout_s=90, raise_on_timeout=False
        )
        assert result is None

    def test_raises_timeout_error_when_raise_on_timeout_true(
        self, jenkins_env, monkeypatch
    ):
        """Raises TimeoutError when raise_on_timeout=True (default) and deadline hits."""
        fake_now = [0.0]
        monkeypatch.setattr(rh.time, "monotonic", lambda: fake_now[0])
        monkeypatch.setattr(rh.time, "sleep", lambda s: fake_now.__setitem__(0, fake_now[0] + s))

        def fake_get(url, auth, timeout):
            fake_now[0] += 200
            return _FakeResponse(json_data={"executable": None, "cancelled": False})

        monkeypatch.setattr(rh.requests, "get", fake_get)

        with pytest.raises(TimeoutError):
            rh.poll_until_released(
                self._QUEUE_URL, site_url=self._HOST, timeout_s=90, raise_on_timeout=True
            )

    def test_polls_build_until_complete(self, jenkins_env, monkeypatch):
        """Waits through in-progress build polls before SUCCESS."""
        responses = [
            _FakeResponse(json_data={"executable": {"url": self._BUILD_URL}, "cancelled": False}),
            _FakeResponse(json_data={"result": None}),   # still running
            _FakeResponse(json_data={"result": None}),   # still running
            _FakeResponse(json_data={"result": "SUCCESS"}),
        ]
        monkeypatch.setattr(rh.requests, "get", self._make_get(responses))
        monkeypatch.setattr(rh.time, "sleep", lambda s: None)

        result = rh.poll_until_released(
            self._QUEUE_URL, site_url=self._HOST, timeout_s=300
        )
        assert result == self._HOST
