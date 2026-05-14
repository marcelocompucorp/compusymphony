#!/usr/bin/env bash
#
# start-symphony.sh — sane env wrapper for `symphony`.
#
# Reason: `before_run` hooks in WORKFLOW.md run in a subshell that does NOT
# propagate `unset` / `export` to the agent spawn (Symphony's claude_coding_agent
# uses `Port.open :spawn_executable` without an explicit `env:` arg, so the
# Claude CLI inherits the env of the BEAM process — i.e., this script's env).
# That means the only point where we can safely filter env vars is here, before
# `exec`ing symphony.
#
# Usage:
#   ./start-symphony.sh                    # uses ./WORKFLOW.md
#   ./start-symphony.sh path/to/other.md   # uses a different workflow file
#
# Required env:
#   OPENCLAW_GH_TOKEN   — bot GitHub PAT for openclawautomation
#                         (lives in ~/.claude/settings.json env block)
#   JIRA_URL, JIRA_USER, JIRA_TOKEN — Jira credentials (same source)
#
# Filtered out (so the agent cannot use them):
#   SENDGRID_API_KEY         — agent doesn't need email; would let it send
#   SENDGRID_BILLING_API_KEY — SendGrid's billing-scoped key (same risk as above)
#   JENKINS_TOKEN            — mostly read, but can trigger builds — keep out of reach
#   NETDATA_CLOUD_TOKEN      — agent uses Loki for logs; would be write to Netdata space
#
# Available to the agent (read-only credentials inherited from ~/.claude/settings.json):
#   LOKI_*                            — logs (read)
#   TEMPO_TOKEN                       — traces (read)
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY — read-only IAM
#   CLOUDFLARE_API_TOKEN              — read-only
#   MONGO_* (host/user/password/etc)  — read-only access to compucorp.sites etc.
#   RDS_* (CIVIPLUS/DEV/STAGING)      — read-only; jump hosts via SSH for tunnel
#

set -euo pipefail

# Resolve the script's own location (not the caller's CWD) so we can launch
# with the repo's WORKFLOW.md by default — robust even when the wrapper is
# invoked via PATH (`start-symphony.sh`) rather than as `./start-symphony.sh`.
# Falls back to python3 on macOS where `readlink -f` may not be available.
__src="${BASH_SOURCE[0]}"
cd "$(dirname "$(readlink -f "$__src" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$__src")")"

WORKFLOW="${1:-./WORKFLOW.md}"

if [ ! -f "$WORKFLOW" ]; then
  echo "FATAL: workflow file not found at $WORKFLOW" >&2
  exit 2
fi

# --- Load env from ~/.claude/settings.json --------------------------------
# Claude auto-injects the `env` block from settings.json into Claude sessions,
# but a regular Terminal doesn't get those vars. To make this wrapper work
# from any shell, we read the block ourselves and export anything that isn't
# already set. We never override what's already in the env — operator can
# always pre-set a different value if they want to.

SETTINGS_JSON="${CLAUDE_SETTINGS_JSON:-$HOME/.claude/settings.json}"

if [ -f "$SETTINGS_JSON" ]; then
  while IFS=$'\t' read -r key value; do
    if [ -z "${!key:-}" ]; then
      export "$key=$value"
    fi
  done < <(python3 -c "
import json, sys
try:
    with open('$SETTINGS_JSON') as f:
        env = json.load(f).get('env', {})
except Exception as e:
    sys.exit(0)
for k, v in env.items():
    if isinstance(v, str) and '\t' not in v and '\n' not in v:
        print(f'{k}\t{v}')
")
fi

# --- Required credentials -------------------------------------------------

if [ -z "${OPENCLAW_GH_TOKEN:-}" ]; then
  echo "FATAL: OPENCLAW_GH_TOKEN is not set." >&2
  echo "       Expected to find it in $SETTINGS_JSON (env block) or pre-exported in your shell." >&2
  echo "       To add it to settings.json: add \"OPENCLAW_GH_TOKEN\": \"github_pat_...\" to the env block," >&2
  echo "       then re-run this script — no terminal restart needed (this wrapper reads settings.json directly)." >&2
  exit 3
fi

if [ -z "${JIRA_URL:-}" ] || [ -z "${JIRA_USER:-}" ] || [ -z "${JIRA_TOKEN:-}" ]; then
  echo "FATAL: JIRA_URL/JIRA_USER/JIRA_TOKEN must all be set." >&2
  exit 3
fi

# --- Force the agent's gh identity to the bot account ---------------------
# The agent CLI inherits this script's env. Setting GH_TOKEN here makes
# `gh` authenticate as openclawautomation inside the agent session,
# bypassing the operator's personal `gh auth` (which would be Marcelo).
export GH_TOKEN="$OPENCLAW_GH_TOKEN"

# Also override git commit author so commits show the bot identity, not the
# operator's `git config user.name`. GH_TOKEN authenticates the *push*, but
# commit metadata is set at commit time from these env vars — without this,
# PRs would show "opened by openclawautomation" but the commits inside would
# show the operator as author.
#
# Email must be the GitHub noreply form `<user-id>+<login>@users.noreply.github.com`
# (NOT the legacy `<login>@users.noreply.github.com`). Reason: when an account
# has "Block command line pushes that expose my email" set, GitHub rejects
# pushes with the legacy form (error GH007). The numeric ID form is always
# safe and links the commit to the account profile. ID for `openclawautomation`
# is 260519389 — retrieved from `gh api users/openclawautomation --jq .id`.
export GIT_AUTHOR_NAME="openclawautomation"
export GIT_AUTHOR_EMAIL="260519389+openclawautomation@users.noreply.github.com"
export GIT_COMMITTER_NAME="openclawautomation"
export GIT_COMMITTER_EMAIL="260519389+openclawautomation@users.noreply.github.com"

# --- Strip credentials the agent must not use in Phase 1 ------------------
unset SENDGRID_API_KEY          # email send
unset SENDGRID_BILLING_API_KEY  # SendGrid billing API (separate key, also "send" scope)
unset JENKINS_TOKEN             # deploys / builds
unset NETDATA_CLOUD_TOKEN       # netdata writes

# --- Pick the local build, not the Homebrew binary ------------------------
# The Homebrew `symphony` was compiled from the upstream sapsaldog source and
# does NOT know about our Jira adapter — it will silently fall back to the
# GitHub tracker. Build a local escript with our changes:
#     cd elixir && mise exec -- mix escript.build
# and this wrapper exec's that one instead. If the local binary is missing,
# we bail out with a clear message rather than silently using the Homebrew
# binary that ignores our adapter.
SYMPHONY_BIN="./elixir/bin/symphony"

if [ ! -x "$SYMPHONY_BIN" ]; then
  echo "FATAL: local symphony build not found at $SYMPHONY_BIN" >&2
  echo "       Build it first with:" >&2
  echo "         cd elixir && mise exec -- mix escript.build" >&2
  echo "       (The Homebrew \`symphony\` binary does NOT include our Jira adapter — using it would silently dispatch as GitHub tracker.)" >&2
  exit 4
fi

# Sanity logging (does not echo any token values).
echo "[start-symphony] env filtered. GH_TOKEN=openclaw-bot, sensitive vars unset."
echo "[start-symphony] WORKFLOW=$WORKFLOW"
echo "[start-symphony] binary=$SYMPHONY_BIN (local build, NOT Homebrew)"
echo "[start-symphony] wrapper-cwd=$(pwd) (agent's actual cwd will be ~/symphony_workspaces/<KEY>/ per workspace)"
echo ""

exec "$SYMPHONY_BIN" \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  "$WORKFLOW"
