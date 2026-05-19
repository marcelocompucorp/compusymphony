"""
analyze-run.py — analysis logic for Symphony agent run transcripts.

Invoked by analyze-run.sh after JSONL discovery. Splits the bash heredoc
out into a real Python module so it can grow without becoming unreadable
and so individual detectors can be unit-tested later.

Invocation:
    python3 analyze-run.py <path-to-jsonl> [workspace-path]

When `workspace-path` is supplied, also validates `<workspace>/AGENT_DONE`
against the schema in WORKFLOW.md §AGENT_DONE schema.

Reads a Claude Code session JSONL and prints a structured audit covering:

  Tier 1 (reviewer compliance — added in Phase A):
    - Did the agent invoke the code reviewer via the Task tool (the
      mechanism WORKFLOW.md mandates) OR via the `/review` slash command
      (legacy fallback)? With what model + subagent_type?
    - How many rounds? Was the output saved as review-result-r<N>.json?

  Existing detectors (ported as-is):
    - Entry-type and tool-use counts
    - Superpowers skills invoked + required-set check
    - Files Read/Written/Edited
    - WebFetch URLs, Bash commands, MCP Atlassian calls
    - dev-ai-playbooks consulted
"""

import json
import os
import re
import sys
from collections import Counter


# Severity rubric mirrors prompts/code-reviewer-schema.json (Compucorp ai-code-review.md)
REVIEWER_SEVERITIES = ("BLOCKER", "WARNING", "SUGGESTION", "QUESTION")

# Required skills from WORKFLOW.md "Required skills" — the ORDER matters:
# investigation → plan → TDD → verification before completion. We check
# both presence (Tier 1) and ordering (Tier 3).
REQUIRED_SKILLS_ORDERED = [
    "superpowers:systematic-debugging",
    "superpowers:writing-plans",
    "superpowers:test-driven-development",
    "superpowers:verification-before-completion",
]
REQUIRED_SKILLS = set(REQUIRED_SKILLS_ORDERED)

# Playbooks: split into "always must read" (any fix) vs "conditional" (when
# touching that surface). Reading a file matches if its path contains the
# playbook filename. WORKFLOW.md "Read the playbooks" section is the source.
ALWAYS_REQUIRED_PLAYBOOKS = [
    "shared-development-guide.md",
    "unit-testing-guide.md",
]
CONDITIONAL_PLAYBOOKS = [
    "civicrm.md",
    "extension.md",
    "ai-code-review.md",
]

# Tests-ran detector. Any Bash command matching one of these is evidence
# that some test execution was attempted (regardless of result).
TEST_COMMAND_PATTERNS = [
    r"\bphpunit\b",
    r"\bpytest\b",
    r"\bmix test\b",
    r"\brspec\b",
    r"\bjest\b",
    r"\bvitest\b",
    r"\bcomposer\s+(?:run-script\s+)?test",
    r"\./scripts/run\.sh",
    r"\bcv\s+php:eval.*test",
]

# When tests were NOT run, the PR body should contain this disclaimer
# (WORKFLOW.md invariant 6, "Don't fake verification").
TESTS_NOT_RUN_DISCLAIMER = re.compile(
    r"tests?\s+not\s+(?:run|executed)\s+locally",
    re.IGNORECASE,
)

# Triage-first check: WORKFLOW.md step 1a says read THIS ticket BEFORE
# any other work. The first MCP Atlassian call must be `getJiraIssue` or
# a comments read for the working ticket — not a generic search, not a
# write. Generic search calls (`search`, `searchJiraIssuesUsingJql`) are
# legitimate but should NEVER come first; if they do, the agent is
# exploring instead of triaging, which violates step 1a.
TRIAGE_FIRST_REQUIRED_PREFIXES = ("getJiraIssue",)
TRIAGE_FIRST_ALLOWED_LATER = (
    "getJiraIssue",
    "search",
    "searchJiraIssuesUsingJql",
    "getAccessibleAtlassianResources",
    "atlassianUserInfo",
)

# Jenkins-write detector (WORKFLOW.md invariant #5 carve-out).
# The ONLY Jenkins jobs the agent may trigger are the two literal paths below.
# Anything else — different job, different verb (DELETE/PUT), or any
# undecorated POST — is a violation. The detector greps Bash command text;
# it sees both `curl -X POST` and Python `requests.post(...)` /
# `trigger_dev_site(` / `trigger_release_devsite(` invocations from
# `repro_helpers`.
JENKINS_DEVSITE_JOB_PATH_SUBSTR = (
    "/job/Test_Jobs"
    "/job/Create%20Dev%20Site%20-%20Client%20Specific%20-%20Pipeline%20Test"
)
# Phase B: Release Dev Site (fix branch released to existing dev site).
JENKINS_RELEASE_JOB_PATH_SUBSTR = (
    "/job/Deployments/job/Dev%20Sites%20-%20Compucontainer"
    "/job/_Release%20Dev%20Site"
)
# Both allowed job paths in one set for uniform lookup.
_JENKINS_ALLOWED_JOB_PATHS = frozenset({
    JENKINS_DEVSITE_JOB_PATH_SUBSTR,
    JENKINS_RELEASE_JOB_PATH_SUBSTR,
})
# Repos the carved-out Jenkins job can actually deploy as a running
# Drupal+Civi site. Anything outside this list, even on the correct job
# path, is a workflow violation. Keep in sync with WORKFLOW.md invariant #5.
SITE_DEPLOYABLE_REPOS = frozenset({
    "ase", "ies", "eseb", "ciwem", "dta", "drw-website", "tcos", "irs",
    "hse_dais_documents", "hse_dais_main_app", "hse_dais_merge",
    "civiplus-distribution", "core-website", "mm", "cst",
})
# Verbs that constitute a write. GET is read; HEAD is read.
_JENKINS_WRITE_VERB_RE = re.compile(
    r"(?:-X\s*['\"]?(?:POST|PUT|DELETE|PATCH)|"
    r"requests\.(?:post|put|delete|patch)\s*\()",
    re.IGNORECASE,
)
# A `curl` to a Jenkins URL without -X defaults to GET, but with --data /
# --data-urlencode / -d it's a POST. Catch that variant too.
_JENKINS_CURL_DATA_RE = re.compile(
    r"curl\b[^\n]*(?:--data(?:-urlencode|-binary|-raw)?|(?<![A-Za-z])-d\s)",
    re.IGNORECASE,
)
# Substring that signals the command is touching Jenkins at all. We accept
# `$JENKINS_URL`, an explicit `jenkins.` hostname, or a Jenkins-style `/job/`
# path. Combined with the verb gate above, this minimises false positives.
_JENKINS_HOST_RE = re.compile(
    r"(?:\$JENKINS_URL|jenkins\.[a-z0-9.-]+|/job/[A-Za-z0-9%_.-])",
    re.IGNORECASE,
)
# Repo-name extraction from a recorded Bash command. Handles raw curl
# (--data-urlencode "git_repo=git@github.com:compucorp/<name>.git") and
# Python helper calls (trigger_dev_site(git_repo="git@github.com:compucorp/<name>.git"...).
# Terminator is "any non-repo-name character" — covers `.git`, quote chars,
# space, `&` (urlencoded body separator), `,` (Python kwarg separator), etc.
_GIT_REPO_RE = re.compile(
    r"git@github\.com[:/]compucorp/([A-Za-z0-9._-]+?)(?:\.git\b|[^A-Za-z0-9._-])",
    re.IGNORECASE,
)


def extract_devsite_trigger_repo(cmd):
    """Pull the compucorp/<repo> short name out of a recorded Bash command.

    Returns None if no `git@github.com:compucorp/<repo>` pattern is present.
    Used by the audit to cross-check the trigger's target against
    SITE_DEPLOYABLE_REPOS — without this, the audit would silently pass a
    dev-site trigger for an extension or infra repo.
    """
    m = _GIT_REPO_RE.search(cmd or "")
    return m.group(1) if m else None


# Python-execution sniffer. The carve-out trusts the `trigger_dev_site(`
# helper only when it's actually being EXECUTED by Python — not appearing
# as a string inside `echo`, `grep`, a heredoc, or any other quoting
# construct. We detect this by checking that the effective command (after
# stripping leading whitespace, env-var assignments, and `cd <path> &&`
# prefixes) starts with `python` or `python3`.
#
# THREAT MODEL: the audit's job is to catch a CONFUSED agent (or a
# regression in WORKFLOW.md prose causing the agent to invoke Jenkins
# incorrectly), NOT an adversarial actor. Known bypasses that we DON'T
# attempt to catch:
#   - `bash -c "python3 -c '...trigger_dev_site(...'"` — first token is
#     `bash`, so we miss the call entirely (silently). The actual Jenkins
#     trigger still fires; the audit just doesn't record it.
#   - `$(echo python3) -c "..."` — shell command substitution; same.
#   - Two-step: `tee script.py <<<"trigger_dev_site(..." && python3 script.py`
#     — second command is `python3 script.py`, no helper name visible.
# In all cases the SITE_DEPLOYABLE_REPOS allowlist (enforced via Jenkins
# job ACL + the Bash audit + WORKFLOW.md prose) provides defence in depth.
# If adversarial prompt-injection becomes a real concern, replace this
# detector with structured tool-call inspection (the JSONL records
# subagent_type/name/input on every dispatch — that's a tamper-evident
# channel the Bash text isn't).
_LEADING_ENV_RE = re.compile(r"^\s*(?:[A-Z_][A-Z0-9_]*=\S+\s+)+")
_LEADING_CD_RE = re.compile(r"^\s*cd\s+\S+(?:\s*&&\s*|\s*;\s*)", re.IGNORECASE)
_PYTHON_HEAD_RE = re.compile(r"^\s*python3?\b", re.IGNORECASE)


def _is_python_execution(cmd):
    """True iff cmd's first effective token is `python` or `python3`.

    Tolerates leading env-var assignments (`FOO=bar python3 ...`) and a
    leading `cd <path> &&` (common idiom). Anything more nested — `echo`,
    `grep`, `cat`, `bash -c "..."` — does NOT count, even if the inner
    string happens to contain `python3 ...`.
    """
    if not cmd:
        return False
    stripped = cmd
    # Repeatedly strip leading wrappers in a fixed-point loop. Each pass:
    # leading whitespace → env vars → cd-and-chain. Stop when nothing changes.
    while True:
        new = _LEADING_ENV_RE.sub("", stripped, count=1)
        new = _LEADING_CD_RE.sub("", new, count=1)
        if new == stripped:
            break
        stripped = new
    return bool(_PYTHON_HEAD_RE.match(stripped))


def detect_jenkins_writes(bash_commands):
    """Partition Bash commands into Jenkins-write attempts allowed vs disallowed.

    Returns: {"allowed": [(idx, cmd), ...], "disallowed": [(idx, cmd), ...]}

    A command counts as a Jenkins write iff:
      - it contains a Jenkins-host signal (`$JENKINS_URL`, `jenkins.<host>`,
        or `/job/...`), AND
      - it uses a write verb (`-X POST/PUT/DELETE/PATCH`, or `--data`/`-d`
        on a curl, or `requests.post/put/delete/patch(`), OR it invokes one
        of the two carved-out helpers (`trigger_dev_site(` or
        `trigger_release_devsite(`) directly.

    A write is `allowed` iff it ALSO targets one of the two canonical job path
    substrings AND uses POST (not DELETE/PUT/PATCH) — the carve-out is for
    build triggers only. Helper calls are unconditionally allowed because the
    helpers are hard-coded to the carved-out job paths.

    Everything else that satisfies the write-attempt definition is disallowed.
    """
    allowed = []
    disallowed = []
    for entry in bash_commands:
        idx, _desc, cmd = entry

        # --- Path A: Python execution of a carved-out helper ---
        # Only counts if the cmd's effective first token is `python`/`python3`
        # (after stripping env vars + `cd ... &&`). A bare helper name inside
        # echo / grep / cat-heredoc / bash-c does NOT match.
        is_create_helper = "trigger_dev_site(" in cmd
        is_release_helper = "trigger_release_devsite(" in cmd
        if (is_create_helper or is_release_helper) and _is_python_execution(cmd):
            if is_create_helper:
                # Even via the helper, must target an allowlisted repo. The
                # helper itself is hard-coded to the carved-out job path, so
                # the only remaining axis to gate is `git_repo`.
                repo = extract_devsite_trigger_repo(cmd)
                if repo is None or repo in SITE_DEPLOYABLE_REPOS:
                    # repo=None: legitimate when tests/docs use a placeholder
                    # like git_repo="g"; reporter will WARN that it couldn't
                    # cross-check. Don't reclassify as disallowed.
                    allowed.append((idx, cmd))
                else:
                    disallowed.append((idx, cmd))
            else:
                # Release helper: no git_repo param — just allow it.
                allowed.append((idx, cmd))
            continue

        # --- Path B: generic Jenkins write detection ---
        touches_jenkins = bool(_JENKINS_HOST_RE.search(cmd))
        if not touches_jenkins:
            continue
        is_write = bool(_JENKINS_WRITE_VERB_RE.search(cmd)) or \
                   bool(_JENKINS_CURL_DATA_RE.search(cmd))
        if not is_write:
            continue

        # Classify against the carve-out.
        targets_allowed_job = any(path in cmd for path in _JENKINS_ALLOWED_JOB_PATHS)
        # The carve-out is ONLY for build triggers. The URL must end in
        # `/buildWithParameters`. `/disable`, `/<N>/stop`, `/config.xml`,
        # bare `/build` etc. on the same job path are still disallowed —
        # `/build` (no `WithParameters`) doesn't accept the params we need
        # and would silently skip them on real Jenkins.
        is_build_trigger_endpoint = "/buildWithParameters" in cmd
        # Verb: requests.post() / -X POST / curl --data (defaults to POST).
        is_post = (
            re.search(r"-X\s*['\"]?POST\b", cmd, re.IGNORECASE) is not None
            or "requests.post(" in cmd
            or (
                _JENKINS_CURL_DATA_RE.search(cmd) is not None
                and re.search(r"-X\s*['\"]?(?:PUT|DELETE|PATCH)", cmd, re.IGNORECASE) is None
            )
        )

        if targets_allowed_job and is_post and is_build_trigger_endpoint:
            # Cross-check repo allowlist for Create job only.
            if JENKINS_DEVSITE_JOB_PATH_SUBSTR in cmd:
                repo = extract_devsite_trigger_repo(cmd)
                if repo is None or repo in SITE_DEPLOYABLE_REPOS:
                    allowed.append((idx, cmd))
                else:
                    disallowed.append((idx, cmd))
            else:
                # Release job: no git_repo to cross-check — allow.
                allowed.append((idx, cmd))
        else:
            disallowed.append((idx, cmd))
    return {"allowed": allowed, "disallowed": disallowed}


def parse_jsonl(path):
    """Yield (idx, parsed_entry) for each non-empty line in the JSONL."""
    with open(path) as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                yield idx, json.loads(line)
            except json.JSONDecodeError:
                continue


def has_slash_command(entry, cmd):
    """
    Real slash-command invocations: `type=user`, `message.content` is a
    STRING that STARTS WITH `<command-name>/<cmd></command-name>`.

    Anywhere else this substring appears (tool_result content, agent text
    describing the detector, review text quoting an example) is NOT a real
    invocation — hence the start-of-string check.
    """
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    needle = f"<command-name>{cmd}</command-name>"
    if isinstance(content, str):
        return content.lstrip().startswith(needle)
    return False


def assistant_content_blocks(entry):
    if entry.get("type") != "assistant":
        return []
    content = entry.get("message", {}).get("content", [])
    return content if isinstance(content, list) else []


def analyze(path):
    entries = list(parse_jsonl(path))

    types = Counter()
    tools = Counter()
    skills = []               # list of (idx, skill_name) — order preserved for Tier 3
    webfetches = []
    files_read = []
    files_written = []
    files_edited = []
    bash_commands = []        # list of (idx, desc, cmd) — idx kept for ordering checks
    todowrites = 0
    jira_mcp_calls = []       # list of (idx, name)
    text_blocks = 0
    text_sample = []

    # Tier 1: reviewer compliance — Phase A addition. Detect both /review
    # slash command (legacy) AND Task/Agent tool dispatches.
    review_slash_invocations = []
    reviewer_dispatches = []

    # Tier 2: cross-check addressed-vs-ignored. Track Edit/Write events
    # WITH their idx so we can correlate "did agent edit X after reviewer
    # flagged X".
    edits_with_idx = []       # list of (idx, file_path) — Edit + Write merged
    gh_pr_create_invocations = []  # list of (idx, body_text_excerpt)

    for idx, entry in entries:
        types[entry.get("type", "?")] += 1

        if has_slash_command(entry, "/review"):
            review_slash_invocations.append(idx)

        for block in assistant_content_blocks(entry):
            btype = block.get("type", "?")

            if btype == "text":
                text_blocks += 1
                text = block.get("text", "")
                if text and len(text_sample) < 5:
                    text_sample.append(text[:200].replace("\n", " "))
                continue

            if btype != "tool_use":
                continue

            name = block.get("name", "?")
            tools[name] += 1
            inp = block.get("input", {}) or {}

            if name == "Skill":
                skill_name = inp.get("skill", "?")
                skills.append((idx, skill_name))
            elif name == "WebFetch":
                webfetches.append(inp.get("url", "?"))
            elif name == "Read":
                files_read.append(inp.get("file_path", "?"))
            elif name == "Write":
                fpath = inp.get("file_path", "?")
                files_written.append(fpath)
                edits_with_idx.append((idx, fpath))
            elif name == "Edit":
                fpath = inp.get("file_path", "?")
                files_edited.append(fpath)
                edits_with_idx.append((idx, fpath))
            elif name == "Bash":
                desc = (inp.get("description", "") or "")[:60]
                cmd = (inp.get("command", "") or "")[:400].replace("\n", " ⏎ ")
                bash_commands.append((idx, desc, cmd))
                # Detect `gh pr create` invocations + capture --body when present
                if re.search(r"\bgh\s+pr\s+create\b", cmd):
                    # try to extract the --body argument value
                    m = re.search(r"--body(?:-file)?\s+[\"']?([^\"'\n]+)", cmd)
                    body_excerpt = m.group(1)[:200] if m else cmd
                    gh_pr_create_invocations.append((idx, body_excerpt))
            elif name == "TodoWrite":
                todowrites += 1
            elif name in ("Agent", "Task"):
                desc = (inp.get("description", "") or "").lower()
                prompt = inp.get("prompt", "") or ""
                # Tightened reviewer detection: trust description keywords as a hint
                # but ALSO accept any dispatch whose prompt references the canonical
                # prompts/code-reviewer.md file. WORKFLOW.md invariant #9 mandates
                # passing that file; an agent doing self-review by chance won't.
                looks_reviewer = (
                    any(k in desc for k in ("review", "critique", "audit"))
                    or "prompts/code-reviewer.md" in prompt
                    or "code-reviewer-schema.json" in prompt
                )
                reviewer_dispatches.append({
                    "line": idx,
                    "subagent_type": inp.get("subagent_type"),
                    "model": inp.get("model"),
                    "description": inp.get("description"),
                    "looks_reviewer": looks_reviewer,
                })
            elif name.startswith("mcp__") and ("Atlassian" in name or "Jira" in name):
                jira_mcp_calls.append((idx, name.split("__")[-1]))

    section("Summary")
    print(f'Assistant turns:        {types.get("assistant", 0)}')
    print(f'User turns:             {types.get("user", 0)}')
    print(f"Text blocks emitted:    {text_blocks}")
    print(f"Total tool invocations: {sum(tools.values())}")

    section("Tool usage")
    for name, n in tools.most_common():
        print(f"  {name:60s} {n}")

    # Tier 1: skill presence (existing) + Tier 3: skill ORDER (new)
    skill_names = [name for _idx, name in skills]
    section("Superpowers skills invoked (presence + order)")
    if not skills:
        print("  ⚠️  NONE — the agent did not use any Skill.")
        print(f"     WORKFLOW.md requires: {', '.join(REQUIRED_SKILLS_ORDERED)}")
    else:
        for _idx, s in skills:
            marker = "✓" if s in REQUIRED_SKILLS else " "
            print(f"  {marker} {s}")
        missing = REQUIRED_SKILLS - set(skill_names)
        if missing:
            print("")
            print("  ⚠️  Missing required skills:")
            for m in sorted(missing):
                print(f"     - {m}")
        else:
            print("  ✓ All required skills were invoked.")

        # Tier 3 skill order: WORKFLOW.md prescribes
        # systematic-debugging → writing-plans → TDD → verification-before-completion.
        # Build the first-invocation index per required skill; check monotonic.
        first_idx = {}
        for idx, s in skills:
            if s in REQUIRED_SKILLS and s not in first_idx:
                first_idx[s] = idx
        if set(first_idx.keys()) == REQUIRED_SKILLS:
            ordered_actual = sorted(first_idx.keys(), key=lambda k: first_idx[k])
            if ordered_actual == REQUIRED_SKILLS_ORDERED:
                print("  ✓ Skills invoked in the prescribed order.")
            else:
                print("")
                print("  ⚠️  Skills invoked OUT OF ORDER:")
                print(f"     expected: {' → '.join(REQUIRED_SKILLS_ORDERED)}")
                print(f"     actual:   {' → '.join(ordered_actual)}")

    section("Self-review / reviewer subagent evidence")
    reviewer_like = [d for d in reviewer_dispatches if d["looks_reviewer"]]
    slash_count = len(review_slash_invocations)
    sub_count = len(reviewer_like)

    if slash_count == 0 and sub_count == 0:
        print("  ❌ Agent SKIPPED reviewer entirely.")
        print("     WORKFLOW.md step 12a / invariant #9 require a code review pass")
        print("     before opening the PR (Task subagent dispatch with")
        print("     prompts/code-reviewer.md, model: opus, subagent_type: Plan).")
    else:
        if slash_count:
            print(f"  ⚠️  /review slash command invoked {slash_count}x (legacy mechanism — NOT the mandated path).")
            print("     WORKFLOW.md invariant #9 requires a Task subagent dispatch, not just /review.")
        if sub_count:
            print(f"  ✓ Task/Agent reviewer dispatches: {sub_count}")
            for d in reviewer_like:
                model = d["model"] or "default"
                sub = d["subagent_type"] or "?"
                # Per invariant #9: subagent_type SHOULD be Plan, model SHOULD be opus
                model_ok = model == "opus"
                sub_ok = sub == "Plan"
                ann = "✓" if (model_ok and sub_ok) else "⚠️"
                print(f"     {ann} subagent_type={sub} model={model}: {d['description']!r}")
                if not model_ok:
                    print(f"        (WORKFLOW.md invariant #9 prescribes model=opus)")
                if not sub_ok:
                    print(f"        (WORKFLOW.md invariant #9 prescribes subagent_type=Plan for read-only enforcement)")
        if reviewer_dispatches and not reviewer_like:
            print(f"  ℹ️  Other Task/Agent dispatches ({len(reviewer_dispatches)}) — not flagged as review by heuristic:")
            for d in reviewer_dispatches[:5]:
                print(f"     - {d['description']!r} (subagent={d['subagent_type']})")

    # Tier 2: cross-check addressed-vs-ignored. For each reviewer dispatch,
    # check whether the agent edited files AFTER it. We can't match
    # finding-paths without parsing tool_results — but we can flag the
    # ordering: "reviewer was dispatched but no Edit/Write came after" is a
    # strong signal the agent didn't act on findings.
    if reviewer_like:
        section("Reviewer follow-through (Tier 2)")
        for d in reviewer_like:
            after = [e for e in edits_with_idx if e[0] > d["line"]]
            if after:
                print(f"  ✓ After reviewer dispatch at line {d['line']}: {len(after)} edits/writes followed")
            else:
                print(f"  ⚠️  After reviewer dispatch at line {d['line']}: NO follow-up edits/writes — findings may be unaddressed")

    # Tier 3: playbook split. shared-development-guide.md + unit-testing-guide.md
    # are ALWAYS required. civicrm.md / extension.md / ai-code-review.md are conditional.
    section("Playbooks consulted (Tier 3 — split)")
    read_set = " ".join(files_read)

    def read_match(needle):
        return any(needle in path for path in files_read)

    for pb in ALWAYS_REQUIRED_PLAYBOOKS:
        if read_match(pb):
            print(f"  ✓ (always-required) {pb}")
        else:
            print(f"  ⚠️  MISSING (always-required) {pb}")
    for pb in CONDITIONAL_PLAYBOOKS:
        if read_match(pb):
            print(f"  ✓ (conditional, read) {pb}")
        else:
            print(f"  · (conditional, not read) {pb}")

    # Tier 3: tests-actually-ran detector + cross-check disclaimer.
    section("Test execution evidence (Tier 3)")
    test_runs = [
        (idx, desc, cmd)
        for idx, desc, cmd in bash_commands
        if any(re.search(p, cmd) for p in TEST_COMMAND_PATTERNS)
    ]
    if test_runs:
        print(f"  ✓ {len(test_runs)} apparent test execution(s):")
        for idx, desc, cmd in test_runs[:5]:
            print(f"     - line {idx}: {cmd[:120]}")
    else:
        # No tests ran — does any agent text or PR body mention the disclaimer?
        disclaimer_found = any(
            TESTS_NOT_RUN_DISCLAIMER.search(t) for t in text_sample
        )
        if disclaimer_found:
            print("  ⚠️  No tests executed locally. Disclaimer 'Tests not run locally' found in agent output ✓")
        else:
            print("  ⚠️  No tests executed locally AND no 'Tests not run locally' disclaimer detected.")
            print("     WORKFLOW.md invariant 6 requires the disclaimer in the PR Comments section.")

    # Tier 3: triage-first check. WORKFLOW.md step 1a requires reading the
    # ticket BEFORE any other work. The first MCP Atlassian call should be a
    # get/read, not a write. We also check that no Bash/Read/Edit happens
    # before the first ticket read.
    section("Triage-first check (Tier 3)")
    first_jira = jira_mcp_calls[0] if jira_mcp_calls else None
    if not first_jira:
        print("  ⚠️  No Atlassian MCP calls detected — agent did not read the ticket?")
    else:
        first_idx, first_name = first_jira
        # Find the first non-trivial tool call (Bash/Read/Edit/Skill/Write)
        first_work = None
        for idx, entry in entries:
            for block in assistant_content_blocks(entry):
                if block.get("type") == "tool_use":
                    n = block.get("name", "")
                    if n in ("Bash", "Read", "Edit", "Write", "Skill"):
                        first_work = (idx, n)
                        break
            if first_work:
                break

        ticket_read_ok = any(req in first_name for req in TRIAGE_FIRST_REQUIRED_PREFIXES)
        if not ticket_read_ok:
            allowed_later_ok = any(allowed in first_name for allowed in TRIAGE_FIRST_ALLOWED_LATER)
            if allowed_later_ok:
                print(f"  ⚠️  First MCP call was {first_name!r} (line {first_idx}) — that's a generic search/lookup, not THIS ticket's read.")
                print("     WORKFLOW.md step 1a requires `getJiraIssue` (or comments read for the working ticket) as the FIRST MCP call.")
            else:
                print(f"  ⚠️  First MCP call was {first_name!r} (line {first_idx}), not a ticket-read operation.")
        elif first_work and first_work[0] < first_idx:
            print(f"  ⚠️  Agent did {first_work[1]} (line {first_work[0]}) BEFORE reading the ticket via MCP (line {first_idx}).")
            print("     WORKFLOW.md step 1a requires reading the ticket first.")
        else:
            print(f"  ✓ First MCP call: {first_name} at line {first_idx} — ticket-read happened before other work.")

    # Tier 3: gh pr URL ↔ Jira comment cross-check.
    section("PR creation + Jira link cross-check (Tier 3)")
    if not gh_pr_create_invocations:
        print("  ⚠️  No `gh pr create` invocation detected. Either:")
        print("     - The agent escalated (invariant #9: reject after 3 rounds → no PR) ✓")
        print("     - OR the agent abandoned silently (audit failure)")
    else:
        print(f"  ✓ `gh pr create` invoked {len(gh_pr_create_invocations)}x")

    # Tier 3: Jenkins-write audit (WORKFLOW.md invariant #5 carve-out).
    # Four layered checks:
    #   (a) No disallowed Jenkins writes (any verb to any non-devsite job).
    #   (b) At most two allowed triggers per run: 0 (skipped), 1 (Phase A
    #       completed + Phase B skipped), or 2 (full success). > 2 = violation.
    #   (c) Triggers happened after the reviewer approved AND before
    #       `gh pr create` — per the post-approve placement in step 12b-bis.
    #       Order: Create trigger before Release trigger.
    #   (d) The dev-site hostname (xxx.cc-test.site) appears in the
    #       gh pr create `--body` excerpt when a trigger fired.
    section("Jenkins-write audit (Tier 3 — invariant #5 carve-out)")
    jenkins_writes = detect_jenkins_writes(bash_commands)
    allowed_writes = jenkins_writes["allowed"]
    disallowed_writes = jenkins_writes["disallowed"]

    if disallowed_writes:
        print(f"  ❌ {len(disallowed_writes)} DISALLOWED Jenkins write(s) — invariant #5 violation:")
        for idx, cmd in disallowed_writes[:5]:
            print(f"     line {idx}: {cmd[:160]}")
        if len(disallowed_writes) > 5:
            print(f"     ... and {len(disallowed_writes) - 5} more")
    else:
        print("  ✓ No disallowed Jenkins writes.")

    if not allowed_writes:
        print("  · No dev-site triggers in this run (acceptable for non-site repos / dry-skip).")
    elif len(allowed_writes) > 2:
        print(f"  ❌ {len(allowed_writes)} allowed triggers in one run — expected ≤ 2 (Create + Release).")
        for idx, cmd in allowed_writes:
            print(f"     line {idx}: {cmd[:160]}")
    else:
        n = len(allowed_writes)
        print(f"  ✓ {n} dev-site trigger(s) — {'Phase A only' if n == 1 else 'Phase A + Phase B'} (line(s): {', '.join(str(i) for i, _ in allowed_writes)}).")

        # SITE_DEPLOYABLE_REPOS is enforced inside detect_jenkins_writes —
        # anything classified `allowed` already passed the allowlist. Report
        # repo name for operator clarity (only applicable to Create trigger).
        create_triggers = [(i, c) for i, c in allowed_writes if JENKINS_DEVSITE_JOB_PATH_SUBSTR in c
                           or ("trigger_dev_site(" in c and "trigger_release_devsite(" not in c)]
        release_triggers = [(i, c) for i, c in allowed_writes if JENKINS_RELEASE_JOB_PATH_SUBSTR in c
                            or "trigger_release_devsite(" in c]
        for idx, cmd in create_triggers:
            repo = extract_devsite_trigger_repo(cmd)
            if repo is not None:
                print(f"     Create trigger (line {idx}): repo `{repo}` (in SITE_DEPLOYABLE_REPOS).")
            else:
                print(f"  ⚠️  Create trigger (line {idx}) lacks a compucorp/<repo> reference — allowlist check inconclusive.")
        for idx, _cmd in release_triggers:
            print(f"     Release trigger (line {idx}): _Release Dev Site (no repo to cross-check).")

        # Order: Create before Release.
        if create_triggers and release_triggers:
            create_idx = create_triggers[0][0]
            release_idx = release_triggers[0][0]
            if release_idx < create_idx:
                print(f"  ⚠️  Release trigger (line {release_idx}) precedes Create trigger (line {create_idx}) — wrong order.")
            else:
                print(f"  ✓ Create trigger precedes Release trigger.")

        # Ordering vs reviewer and gh pr create: use the first trigger for the
        # pre-pr-create check (both must be before gh pr create) and the last
        # trigger for the post-reviewer check.
        first_trigger_idx = allowed_writes[0][0]
        last_trigger_idx = allowed_writes[-1][0]

        last_reviewer_line = max(
            (d["line"] for d in reviewer_dispatches if d["looks_reviewer"]),
            default=None,
        )
        if last_reviewer_line is not None and first_trigger_idx < last_reviewer_line:
            print(f"  ⚠️  First trigger at line {first_trigger_idx} precedes the reviewer dispatch at line {last_reviewer_line}.")
            print("     WORKFLOW.md step 12b-bis runs after reviewer approval, before `gh pr create`.")
        elif last_reviewer_line is not None:
            print(f"  ✓ Triggers happened after reviewer dispatch (line {last_reviewer_line}).")

        if gh_pr_create_invocations:
            first_pr = gh_pr_create_invocations[0][0]
            if last_trigger_idx > first_pr:
                print(f"  ⚠️  Last trigger at line {last_trigger_idx} happens AFTER `gh pr create` at line {first_pr}.")
                print("     The dev-site URL won't be in the PR body — placement is wrong.")
            else:
                print(f"  ✓ All triggers precede `gh pr create` (line {first_pr}).")

        # Hostname-in-PR-body: scan all gh pr create body excerpts for a
        # *.cc-test.site hostname reference.
        hostname_re = re.compile(r"[a-z0-9-]+(?:\.public)?\.cc-test\.site", re.IGNORECASE)
        body_has_hostname = any(
            hostname_re.search(body) for _i, body in gh_pr_create_invocations
        )
        if gh_pr_create_invocations:
            if body_has_hostname:
                print("  ✓ Dev-site hostname referenced in PR body.")
            else:
                print("  ⚠️  Triggered a dev site but PR body has no `*.cc-test.site` reference.")
                print("     WORKFLOW.md step 12b-bis requires the URL be embedded for human reviewers.")

    # Tier 3: tokens by tool (proxy for cost attribution).
    section("Token usage by tool (Tier 3, approximation)")
    tool_tokens = aggregate_tokens_by_tool(entries)
    if tool_tokens:
        for tool, tokens in sorted(tool_tokens.items(), key=lambda kv: -kv[1]):
            print(f"  {tool:40s} ~{tokens:>10,} output tokens")
    else:
        print("  (no usage events captured)")

    section(f"Files Read ({len(files_read)})")
    for f in files_read[:30]:
        print(f"  {f}")
    if len(files_read) > 30:
        print(f"  ... and {len(files_read) - 30} more")

    section(f"Files Written ({len(files_written)})")
    for f in files_written:
        print(f"  {f}")

    section(f"Files Edited ({len(files_edited)})")
    for f in files_edited:
        print(f"  {f}")

    section(f"WebFetch URLs ({len(webfetches)})")
    for u in webfetches:
        print(f"  {u}")

    section(f"Bash commands ({len(bash_commands)})")
    for i, (idx, desc, cmd) in enumerate(bash_commands, 1):
        print(f"  [{i:>3}] {desc}")
        print(f"         $ {cmd}")

    section(f"Atlassian MCP calls ({len(jira_mcp_calls)})")
    for _idx, c in jira_mcp_calls:
        print(f"  {c}")

    section("First 5 text outputs from agent (truncated)")
    for i, t in enumerate(text_sample, 1):
        print(f"  [{i}] {t}")

    print("\n" + "=" * 67)


def aggregate_tokens_by_tool(entries):
    """
    Approximation: attribute each assistant turn's output_tokens to the
    first tool_use that turn invoked. Many turns mix text + tool_use; this
    gives a rough where-the-budget-went view, not exact per-tool cost.
    """
    by_tool = Counter()
    for _idx, entry in entries:
        if entry.get("type") != "assistant":
            continue
        usage = entry.get("message", {}).get("usage") or {}
        out = usage.get("output_tokens", 0)
        if not out:
            continue
        first_tool = None
        for block in assistant_content_blocks(entry):
            if block.get("type") == "tool_use":
                first_tool = block.get("name", "?")
                break
        if first_tool:
            by_tool[first_tool] += out
        else:
            by_tool["(text only)"] += out
    return by_tool


def section(title):
    print(f"\n--- {title} ---")


AGENT_DONE_PREFIXES = ("success", "dry-run", "blocked-review", "blocked")
AGENT_DONE_ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$"
)


def validate_agent_done(workspace, expected_issue_key=None):
    """Validate <workspace>/AGENT_DONE against the schema in WORKFLOW.md.

    Returns a list of finding strings — empty list = OK.
    """
    path = os.path.join(workspace, "AGENT_DONE")
    if not os.path.isfile(path):
        return [f"MISSING: {path} not present (run did not reach a terminal state)"]

    try:
        with open(path) as fh:
            raw = fh.read()
    except OSError as e:
        return [f"UNREADABLE: {path}: {e}"]

    lines = raw.splitlines()
    findings = []
    if len(lines) > 1:
        findings.append(f"MULTI_LINE: AGENT_DONE has {len(lines)} lines; expected exactly 1")
    if lines and not raw.endswith("\n"):
        findings.append("MISSING_TRAILING_NEWLINE: AGENT_DONE should end with a newline")

    line = lines[0] if lines else ""
    parts = line.split(" ")
    if len(parts) != 3:
        findings.append(
            f"MALFORMED: expected 3 space-separated fields, got {len(parts)}: {line!r}"
        )
        return findings

    prefix, ts, key = parts
    if prefix not in AGENT_DONE_PREFIXES:
        findings.append(
            f"BAD_PREFIX: {prefix!r} — expected one of {AGENT_DONE_PREFIXES}"
        )
    if not AGENT_DONE_ISO8601.match(ts):
        findings.append(f"BAD_TIMESTAMP: {ts!r} is not ISO-8601")
    if expected_issue_key and key != expected_issue_key:
        findings.append(
            f"KEY_MISMATCH: AGENT_DONE says {key!r}, workspace expects {expected_issue_key!r}"
        )
    return findings


def main(argv):
    if len(argv) < 2 or len(argv) > 3:
        print(
            "Usage: python3 analyze-run.py <path-to-jsonl> [workspace-path]",
            file=sys.stderr,
        )
        return 1
    analyze(argv[1])
    if len(argv) == 3 and argv[2]:
        workspace = argv[2]
        expected_key = os.path.basename(workspace.rstrip("/")) or None
        section("AGENT_DONE schema check")
        findings = validate_agent_done(workspace, expected_issue_key=expected_key)
        if not findings:
            print(f"  ✓ {os.path.join(workspace, 'AGENT_DONE')} conforms to schema (WORKFLOW.md §AGENT_DONE schema).")
        else:
            for f in findings:
                print(f"  ⚠️ {f}")
            print(
                "     Schema: '<success|dry-run|blocked-review|blocked> <ISO-8601> <issue.identifier>'"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
