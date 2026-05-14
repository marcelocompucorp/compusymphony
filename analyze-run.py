"""
analyze-run.py — analysis logic for Symphony agent run transcripts.

Invoked by analyze-run.sh after JSONL discovery. Splits the bash heredoc
out into a real Python module so it can grow without becoming unreadable
and so individual detectors can be unit-tested later.

Invocation:
    python3 analyze-run.py <path-to-jsonl>

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


def main(argv):
    if len(argv) != 2:
        print("Usage: python3 analyze-run.py <path-to-jsonl>", file=sys.stderr)
        return 1
    analyze(argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
