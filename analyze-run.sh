#!/usr/bin/env bash
#
# analyze-run.sh — extract a structured report from a Symphony agent's run.
#
# Claude Code writes a complete JSONL transcript of every agent session to
# ~/.claude/projects/<workspace-id>/<session-id>.jsonl. The Symphony disk_log
# only captures JSON-RPC notification *types* (item/created, usage/update),
# not the actual content — so to see what tools the agent used, which skills
# it invoked, which files it read, what bash commands it ran, you have to
# read the JSONL directly.
#
# This script does that. Pass a Jira ticket key OR a path to a JSONL.
#
# Usage:
#   ./analyze-run.sh COMCL-1442         # auto-find latest session for that workspace
#   ./analyze-run.sh path/to/session.jsonl
#

set -euo pipefail

ARG="${1:-}"
if [ -z "$ARG" ]; then
  echo "Usage: $0 <JIRA-KEY|path-to-jsonl>" >&2
  exit 1
fi

if [ -f "$ARG" ]; then
  JSONL="$ARG"
else
  # Treat as Jira key — find the workspace project dir and pick latest JSONL.
  PROJ_DIR="$HOME/.claude/projects/-Users-mar-symphony-workspaces-${ARG}"
  if [ ! -d "$PROJ_DIR" ]; then
    echo "FATAL: no Claude project dir for $ARG at $PROJ_DIR" >&2
    echo "       Did this ticket actually run, or is the workspace path different?" >&2
    exit 2
  fi
  JSONL=$(ls -t "$PROJ_DIR"/*.jsonl 2>/dev/null | head -1)
  if [ -z "$JSONL" ]; then
    echo "FATAL: no .jsonl found in $PROJ_DIR" >&2
    exit 3
  fi
fi

echo "==================================================================="
echo "Symphony agent run analysis"
echo "==================================================================="
echo "Transcript: $JSONL"
echo "Size:       $(wc -c <"$JSONL" | tr -d ' ') bytes, $(wc -l <"$JSONL" | tr -d ' ') entries"
echo ""

python3 - "$JSONL" <<'PY'
import json, sys
from collections import Counter

path = sys.argv[1]
lines = list(open(path))

# --- entry types ---
types = Counter()
tools = Counter()
skills = []
webfetches = []
files_read = []
files_written = []
files_edited = []
bash_commands = []
todowrites = 0
jira_mcp_calls = []
github_actions = []
text_blocks = 0
text_sample = []

for line in lines:
    try:
        d = json.loads(line)
    except Exception:
        continue
    types[d.get('type', '?')] += 1
    if d.get('type') != 'assistant':
        continue
    msg = d.get('message', {})
    for block in msg.get('content', []):
        btype = block.get('type', '?')
        if btype == 'text':
            text_blocks += 1
            t = block.get('text', '')
            if t and len(text_sample) < 5:
                text_sample.append(t[:200].replace('\n', ' '))
        elif btype == 'tool_use':
            name = block.get('name', '?')
            tools[name] += 1
            inp = block.get('input', {}) or {}
            if name == 'Skill':
                skills.append(inp.get('skill', '?'))
            elif name == 'WebFetch':
                webfetches.append(inp.get('url', '?'))
            elif name == 'Read':
                files_read.append(inp.get('file_path', '?'))
            elif name == 'Write':
                files_written.append(inp.get('file_path', '?'))
            elif name == 'Edit':
                files_edited.append(inp.get('file_path', '?'))
            elif name == 'Bash':
                bash_commands.append((
                    inp.get('description', '')[:60],
                    inp.get('command', '')[:200].replace('\n', ' ⏎ ')
                ))
            elif name == 'TodoWrite':
                todowrites += 1
            elif name.startswith('mcp__') and ('Atlassian' in name or 'Jira' in name):
                jira_mcp_calls.append(name.split('__')[-1])

def section(title):
    print(f'\n--- {title} ---')

# --- summary ---
section('Summary')
print(f'Assistant turns:        {types.get("assistant", 0)}')
print(f'User turns:             {types.get("user", 0)}')
print(f'Text blocks emitted:    {text_blocks}')
print(f'Total tool invocations: {sum(tools.values())}')

section('Tool usage')
for name, n in tools.most_common():
    print(f'  {name:60s} {n}')

# --- skills invoked ---
section('Superpowers skills invoked')
if not skills:
    print('  ⚠️  NONE — the agent did not use any Skill.')
    print('     WORKFLOW.md requires: systematic-debugging, writing-plans, test-driven-development, verification-before-completion')
else:
    required = {
        'superpowers:systematic-debugging',
        'superpowers:writing-plans',
        'superpowers:test-driven-development',
        'superpowers:verification-before-completion',
    }
    for s in skills:
        marker = '✓' if s in required else ' '
        print(f'  {marker} {s}')
    missing = required - set(skills)
    if missing:
        print('')
        print('  ⚠️  Missing required skills:')
        for m in sorted(missing):
            print(f'     - {m}')
    else:
        print('  ✓ All 4 required skills were invoked.')

# --- /review evidence ---
# /review is a slash command. Claude Code encodes slash commands inside user-turn
# `message.content` (string OR list of text blocks) as the literal substring
# `<command-name>/review</command-name>`. Anything else mentioning "review" is
# almost always the agent describing review concepts, not actually invoking the
# slash command.
section('Self-review (/review) evidence')

def has_slash_command(d, cmd):
    # Real slash command invocations: type=user, message.content is a STRING that
    # STARTS WITH `<command-name>/<cmd>`. Verified against actual sessions.
    # Anywhere else the string appears (tool_result content, agent text describing
    # the detector, review text quoting an example) is NOT a real invocation —
    # so the start-of-string check is required to avoid false positives.
    if d.get('type') != 'user':
        return False
    content = d.get('message', {}).get('content')
    needle = f'<command-name>{cmd}</command-name>'
    if isinstance(content, str):
        return content.lstrip().startswith(needle)
    return False

found = any(has_slash_command(json.loads(line), '/review')
            for line in lines
            if line.strip().startswith('{'))

if found:
    print('  ✓ /review slash command was invoked')
else:
    print('  ⚠️  /review was NOT invoked. WORKFLOW.md step 10 requires it.')
    print('     (Detector matches `<command-name>/review</command-name>` in user turns.)')

# --- files touched ---
section(f'Files Read ({len(files_read)})')
for f in files_read[:30]:
    print(f'  {f}')
if len(files_read) > 30:
    print(f'  ... and {len(files_read)-30} more')

section(f'Files Written ({len(files_written)})')
for f in files_written:
    print(f'  {f}')

section(f'Files Edited ({len(files_edited)})')
for f in files_edited:
    print(f'  {f}')

# --- WebFetch ---
section(f'WebFetch URLs ({len(webfetches)})')
for u in webfetches:
    print(f'  {u}')

# --- Bash commands ---
section(f'Bash commands ({len(bash_commands)})')
for i, (desc, cmd) in enumerate(bash_commands, 1):
    print(f'  [{i:>3}] {desc}')
    print(f'         $ {cmd}')

# --- MCP Jira calls ---
section(f'Atlassian MCP calls ({len(jira_mcp_calls)})')
if jira_mcp_calls:
    for c in jira_mcp_calls:
        print(f'  {c}')

# --- text sample ---
section('First 5 text outputs from agent (truncated)')
for i, t in enumerate(text_sample, 1):
    print(f'  [{i}] {t}')

# --- playbook reads ---
section('Compucorp playbooks consulted')
playbook_paths = [f for f in files_read if '.playbooks' in f or 'dev-ai-playbooks' in f]
if playbook_paths:
    for p in playbook_paths:
        print(f'  ✓ {p}')
else:
    print('  ⚠️  No dev-ai-playbooks files were read.')

print('\n' + '='*67)
PY
