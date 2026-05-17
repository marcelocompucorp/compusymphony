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
# This script handles only argument parsing and JSONL discovery; all the
# analysis logic lives in analyze-run.py next to it (so it can grow without
# becoming an unreadable bash heredoc).

set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")")"

ARG="${1:-}"
if [ -z "$ARG" ]; then
  echo "Usage: $0 <JIRA-KEY|path-to-jsonl>" >&2
  exit 1
fi

WORKSPACE=""
if [ -f "$ARG" ]; then
  JSONL="$ARG"
  # Best-effort workspace derivation from a Claude-projects-style path:
  # ~/.claude/projects/-Users-mar-symphony-workspaces-<KEY>/<session>.jsonl → ~/symphony_workspaces/<KEY>
  PROJ_BASENAME="$(basename "$(dirname "$ARG")")"
  if [[ "$PROJ_BASENAME" == "-Users-mar-symphony-workspaces-"* ]]; then
    KEY="${PROJ_BASENAME#-Users-mar-symphony-workspaces-}"
    if [ -d "$HOME/symphony_workspaces/$KEY" ]; then
      WORKSPACE="$HOME/symphony_workspaces/$KEY"
    fi
  fi
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
  if [ -d "$HOME/symphony_workspaces/$ARG" ]; then
    WORKSPACE="$HOME/symphony_workspaces/$ARG"
  fi
fi

echo "==================================================================="
echo "Symphony agent run analysis"
echo "==================================================================="
echo "Transcript: $JSONL"
echo "Size:       $(wc -c <"$JSONL" | tr -d ' ') bytes, $(wc -l <"$JSONL" | tr -d ' ') entries"
echo ""

exec python3 analyze-run.py "$JSONL" "$WORKSPACE"
