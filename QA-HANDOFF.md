# QA handoff — Symphony + Claude Code + Jira (Phase 1)

This document hands off the integration to a human QA for the first real runs. Everything in this doc is "what's true on 2026-05-13" — credentials, paths, repos, and the Phase 1 pilot scope may drift. **Re-read against current state before executing.**

Plan source: `~/.claude/plans/plano-que-vc-mandou-sharded-dewdrop.md`.

---

## 1. What was built

### Adapter Jira (Elixir, in fork `marcelocompucorp/symphony`)

- `elixir/lib/symphony_elixir/jira/config.ex` — reads `jira:` YAML section from `WORKFLOW.md`. Fields: `base_url`, `email`, `api_token`, `project_keys`, `trigger_label`. Resolves `$VAR` references via `System.get_env/1`.
- `elixir/lib/symphony_elixir/jira/client.ex` — REST API client. Uses Jira Cloud REST API v3 endpoint `/rest/api/3/search/jql` (the legacy `/search` was removed in 2025; see CHANGE-2046). Pagination is token-based (`nextPageToken`), not `startAt`. Comments go to v2 (`/rest/api/2/issue/{key}/comment`) which accepts plain wiki markup; v3 requires ADF.
- `elixir/lib/symphony_elixir/jira/tracker.ex` — implements the 7-callback `Tracker` behaviour. Mockable client via `Application.get_env(:symphony_elixir, :jira_client_module, Client)`.
- Modified `elixir/lib/symphony_elixir/tracker.ex` — added `"jira" -> SymphonyElixir.Jira.Tracker` to the dispatch case.
- Modified `elixir/lib/symphony_elixir/config.ex` — added `"jira"` to `@tracker_sections` (so `Config.tracker_kind/0` detects it) and `"jira" -> SymphonyElixir.Jira.Config` to `tracker_config_module/0`.

### Tests

- `elixir/test/symphony_elixir/jira/tracker_test.exs` — 8 tests covering the 7 callbacks + dispatcher routing.
- `elixir/test/symphony_elixir/jira/client_test.exs` — 14 tests covering JQL construction (including cross-project mode and quote-escape in trigger_label), ADF description flattening, pagination via `nextPageToken`, error paths (401, missing token), and transition resolution (matches by `to.name` first).
- Modified `elixir/test/support/test_support.exs` — added `tracker_backend_yaml("jira", config)` clause so other tests can render Jira workflow stubs.

### Prompts and config (at repo root)

- `WORKFLOW.md` — cross-project pilot (no `project_keys` filter; trigger is the label `agent:todo` only, applied to any Jira ticket in any project). Max 1 concurrent agent, max 30 turns. Invokes `superpowers:systematic-debugging`, `writing-plans`, `test-driven-development`, `verification-before-completion`. Enforces commit prefix `{{ issue.identifier }}:`. Repo allowlist hardcoded in the prompt body: `compucorp/ase` and `compucorp/compuclient`. PR base branch determined per-repo via `gh api ... --jq .default_branch` (master for ase, `7.x-7.x` for compuclient — model follows the Compuclient Git Workflow doc).
- `prompts/TOOLS.md` — adapted from `openclaw-configurations/TOOLS.md`. Credentials replaced with env vars; openclaw-specific helpers stripped.
- `prompts/INVESTIGATION.md` — adapted from `openclaw-configurations/incident_check/WORKFLOW.md`. Reframed for bug-fix (reproduce-first) instead of incident analysis.
- `prompts/PLAYBOOKS.md` — short index into `dev-ai-playbooks/.ai/` and slash commands.

### Installed binaries (system-level)

- `symphony` 0.1.1 (Homebrew tap `sapsaldog/symphony`, with Erlang/OTP 28 + Elixir 1.19.5 as deps)
- `symphony-claude` 1.0.0 (Homebrew, with Node as dep)
- `mise` 2026.5.7 (Homebrew, for project-local Elixir version pinning)

---

## 2. What was NOT tested

I (the implementing agent) only tested at the unit + read-only smoke level. **None of the following has been exercised against real Jira or real GitHub:**

- Writing to Jira (transitioning status, posting comments).
- Cloning a repo from the Compucorp org via the agent token.
- Opening a real PR.
- The `superpowers` skills firing in the `symphony-claude` headless session (only validated they load via `claude --print --output-format stream-json` in an isolated terminal; not via the actual Symphony spawn path).
- The actual filtering of `SENDGRID_API_KEY`/`JENKINS_TOKEN`/`NETDATA_CLOUD_TOKEN` reaching the agent (the wrapper `./start-symphony.sh` does this — see §3.1 for how to verify the env in a running session).
- The agent's `gh` operating as `openclawautomation` end-to-end (set by the wrapper — see §3.2 to validate).
- Multi-page `nextPageToken` pagination against real Jira (only tested via mock).
- The status dashboard's `Project` line shows `https://linear.app/...` even when tracker_kind=jira — minor cosmetic bug in `status_dashboard.ex`, not blocking but worth flagging upstream.

---

## 3. Security checklist — validate BEFORE the first real run

### 3.1 Credential model

**ALWAYS launch via the wrapper `./start-symphony.sh`, never via `symphony` directly.** The wrapper does two things relevant to credentials:

```bash
export GH_TOKEN="$OPENCLAW_GH_TOKEN"   # swap operator's gh token for the bot's
# (no env vars are `unset` — see history note below)
exec symphony ... ./WORKFLOW.md
```

**All credentials in `~/.claude/settings.json` pass through to the agent.** Scope is enforced at the upstream service, not by the wrapper:

| Credential | Upstream scope | Verification command (run anytime) |
|---|---|---|
| `SENDGRID_API_KEY` | Read-only (Mail Activity, settings.read, etc. — 64 scopes, all `.read`) | `curl -sH "Authorization: Bearer $SENDGRID_API_KEY" https://api.sendgrid.com/v3/scopes \| jq '.scopes \| map(select(endswith(".read") \| not) \| select(test("read|eligible|2fa_required") \| not))'` should return `[]` |
| `SENDGRID_BILLING_API_KEY` | Read-only (`billing.read` only) | Same scopes endpoint with the billing key |
| `JENKINS_TOKEN` (user: `openclawautomation`) | Restricted role `compucorp*openclaw_automation` (operator-confirmed read-scoped; not API-verifiable without a write probe) | `curl -sS -u "$JENKINS_USER:$JENKINS_TOKEN" "$JENKINS_URL/whoAmI/api/json"` shows the role |
| `NETDATA_CLOUD_TOKEN` | Viewer on `compucorpcluster` space (operator-confirmed; the API exposes `permissions: []` on space membership which suggests viewer but is not definitive) | `curl -sH "Authorization: Bearer $NETDATA_CLOUD_TOKEN" "$NETDATA_CLOUD_URL/api/v2/spaces"` |
| Loki, Tempo, AWS, Cloudflare, MongoDB, RDS | All read-only at upstream | See `prompts/TOOLS.md` for canonical patterns |

**WORKFLOW.md invariant #5** ("no production side effects outside the PR") remains as a second-line, prompt-level defense. The agent is instructed never to attempt write operations even if it has the token. The audit (`./analyze-run.sh <KEY>`) detects all external `curl` calls in the run so you can spot anomalies.

**History note (commits up through `fd476fe`, May 2026):** Earlier the wrapper `unset` four env vars (`SENDGRID_API_KEY`, `SENDGRID_BILLING_API_KEY`, `JENKINS_TOKEN`, `NETDATA_CLOUD_TOKEN`) as belt-and-suspenders. The operator confirmed these tokens are scoped read-only at the upstream service, and the wrapper-side `unset` was removed so the agent can investigate email-delivery, build-status, and infra-metric questions without needing a human handoff. If QA runs against a Symphony build older than `fd476fe`, the legacy unset behavior still applies and absence of these vars is expected.

**Smoke-test the agent's env** once a session is running (verifies the wrapper loaded `~/.claude/settings.json` correctly):

```bash
# Find the claude (symphony-claude) PID:
ps -ef | grep -E 'symphony-claude|claude --' | grep -v grep

# Inspect that process's env (replace <pid>):
ps eww -p <pid> | tr ' ' '\n' | grep -E 'SENDGRID|JENKINS_TOKEN|NETDATA_CLOUD|GH_TOKEN'
# Expected (post-fd476fe):
#   GH_TOKEN=github_pat_...                  (the OPENCLAW token; not your personal gh token)
#   SENDGRID_API_KEY=SG....                   (read-only)
#   SENDGRID_BILLING_API_KEY=SG....           (read-only)
#   JENKINS_TOKEN=...                         (restricted)
#   NETDATA_CLOUD_TOKEN=...                   (read-only)

# Equivalent via /proc on Linux (if not on macOS):
# tr '\0' '\n' < /proc/<pid>/environ | grep -E 'SENDGRID|...'
```

If `GH_TOKEN` is missing or matches your personal `gh auth token` (not the bot's), the wrapper failed to swap identity — `kill -TERM` the symphony process and restart via `./start-symphony.sh`.

### 3.2 GitHub identity — `openclawautomation`

The integration is configured so the agent uses **`openclawautomation`** (the same bot identity already used in Jira) for GitHub writes, instead of inheriting the operator's personal `gh auth`. This keeps a consistent audit trail across both systems ("the agent did X" = "OpenClaw did X").

Implementation:
- The wrapper `./start-symphony.sh` does `export GH_TOKEN="$OPENCLAW_GH_TOKEN"` **in the parent shell** before invoking Symphony — this is the only place where setting `GH_TOKEN` actually reaches the agent. A `before_run` hook does NOT work for this (see §3.1 explanation).
- `OPENCLAW_GH_TOKEN` lives in `~/.claude/settings.json` (`env` block) and is auto-injected per Claude session.

**Validate before the first real run:**

```bash
# Confirm identity is the bot, not your personal account:
curl -sS -H "Authorization: Bearer $OPENCLAW_GH_TOKEN" \
  https://api.github.com/user | jq -r '.login'
# Expected: openclawautomation

# Confirm token grants WRITE access to the allowlisted repos (validated 2026-05-14):
for repo in ase compuclient; do
  echo -n "compucorp/$repo: "
  curl -sS -H "Authorization: Bearer $OPENCLAW_GH_TOKEN" \
    "https://api.github.com/repos/compucorp/$repo" \
    | jq -r '.permissions // "no permissions field — likely token too narrow"'
done
# Expected: {push: true, ...} on both, otherwise the agent cannot open PRs.

# Inspect what scopes the token grants — if it's a classic PAT, this header
# is populated. If it's a fine-grained PAT, scopes are documented in the
# GitHub UI under Settings -> Developer settings -> Personal access tokens.
curl -sS -H "Authorization: Bearer $OPENCLAW_GH_TOKEN" -I \
  https://api.github.com/user 2>&1 | grep -i x-oauth-scopes
```

**Red flags worth addressing before going autonomous:**

**The token is a guardrail only if the agent obeys the prompt. There is no system-level enforcement of the allowlist.** The current `OPENCLAW_GH_TOKEN` is a re-used PAT from the openclaw host that grants `openclawautomation` write access to many `compucorp/*` repos (collaborator on dozens individually). The WORKFLOW.md prompt tells the agent to refuse anything outside `compucorp/ase` + `compucorp/compuclient`, but if the agent drifts — confusing ticket, creative interpretation, prompt injection in a comment — the token will let it clone/push to another repo. The BEAM layer has no idea what the agent is about to do.

**No run should be unattended until either:**
- (A) the token is replaced with a Fine-grained PAT scoped *exclusively* to `compucorp/ase` + `compucorp/compuclient` with `contents:write` + `pull-requests:write` and nothing else, OR
- (B) the orchestrator gains repo-allowlist enforcement at the Elixir layer (future work — would inspect the ticket for repo references and refuse to dispatch if no allowlisted repo matches, before the agent even starts).

For Phase 1 supervised pilot, this is acceptable — you are watching. For autonomous, (A) is the minimum.

- Token expiration is soon or never — set a 90-day expiration as a forcing function for periodic review.

If `OPENCLAW_GH_TOKEN` is unset (e.g. someone removed it from `~/.claude/settings.json`), the wrapper aborts with a clear FATAL message before invoking Symphony — fail-closed, not silent fallback.

### 3.3 Repo allowlist

`WORKFLOW.md` hardcodes the allowlist in the prompt body. Verify the list still matches the pilot scope at run time (it may need updates as the pilot widens).

**Current allowlist (validated 2026-05-14):**
- `compucorp/ase` — default branch `master`. `openclawautomation` has `push: true`.
- `compucorp/compuclient` — default branch `7.x-7.x` (the active major-version branch per the Compuclient Git Workflow doc). `openclawautomation` has `push: true`.

If the pilot expands (e.g. add `compucorp/civiplus-distribution`, an extension repo, or a client repo), three things must move together:
1. Add the repo name in `WORKFLOW.md` (allowlist section in the prompt body).
2. Add `openclawautomation` as a collaborator with `write` on the repo.
3. Validate via the curl loop in §3.2.

### 3.4 Dry-run for the first session

There is no built-in `dry_run` flag. For the first session against a real ticket, override the prompt locally to skip the push/PR step — or run the smoke sandbox first (§4).

### 3.5 Live supervision for the first session

Sit in front of the workspace while Symphony runs the first ticket:

```bash
watch -n 1 ls -la ~/symphony_workspaces/   # see new workspaces appear
watch -n 1 'ps -ef | grep -E "symphony|claude" | grep -v grep'  # see agent process spawn/exit
tail -F ~/.symphony/logs/symphony.log      # if symphony emits logs there
```

Be ready to `kill -TERM` if behavior deviates.

---

## 4. Smoke run — sandbox repo (RECOMMENDED before any real ticket)

Create a throwaway sandbox to exercise the entire pipeline end-to-end without touching real bugs:

1. Create a GitHub repo: `compucorp/agent-sandbox` (or any repo you control).
2. Add a trivial README.
3. Add `openclawautomation` as a collaborator on `compucorp/agent-sandbox` (or use whichever bot identity matches the token currently in `OPENCLAW_GH_TOKEN`).
4. Create a Jira issue in any project you can write to (e.g. `CIVIPLMMSR-TEST-X`) with description: "Add a single line to README.md saying 'Hello from agent'. Open a PR."
5. Add label `agent:todo` to the issue.
6. Edit `WORKFLOW.md` temporarily:
   - In the prompt body, replace the allowlist entries with `compucorp/agent-sandbox`.
   - Leave the JQL cross-project (no `project_keys`) so the trigger picks up your sandbox ticket regardless of which project you used.
7. Start Symphony via the wrapper:
   ```bash
   cd ~/projects/compuco-symphony
   ./start-symphony.sh
   ```
8. Observe:
   - Within ~30s, Symphony logs "Dispatching <KEY>".
   - `~/symphony_workspaces/<KEY>/` appears, with `./.playbooks/` symlink.
   - The agent clones `agent-sandbox` into `./repo/`.
   - A branch `agent/<KEY>-fix` is pushed.
   - A PR appears in `compucorp/agent-sandbox` with the structured body.
   - A Jira comment with the PR link is posted on the ticket.

**Success criterion:** PR exists, README is edited, Jira has the link. Anything else → stop and investigate.

---

## 5. First real pilot ticket

When the smoke run is green:

1. **Choose the ticket** — small, scoped, reproducible. Ideally a known-historical bug you can compare the agent's PR against. **Not** an active production-blocking incident.
2. **Confirm the WORKFLOW.md allowlist matches the target repo.**
3. **Apply label `agent:todo` to the Jira ticket.**
4. **Start Symphony with live supervision** (§3.5).
5. **Observe per-stage:**
   - Logs Symphony (terminal 1)
   - Jira ticket page in browser (terminal 2)
   - `watch ls ~/symphony_workspaces/<KEY>/` (terminal 3)
   - `tail -F` agent logs if available
6. **Compare PR vs expectation.** Subjective gate: if reviewing the PR would take >30 min of human re-work, this is a failure for Phase 1.

### Cost observation

Each `symphony-claude` turn emits `cost_usd` in the `turn/completed` event. Capture the total cost per ticket. Suggested cap for Phase 1: **$5/ticket**. If a single session exceeds that, the agent is probably stuck in a loop — kill it and investigate.

---

## 6. Failure modes worth forcing

Before widening the pilot, deliberately trigger these once:

| Scenario | How | Expected |
|---|---|---|
| Agent exceeds `max_turns` | Ticket with vague/impossible description | Symphony marks failed, workspace is released, no PR created |
| Jira API 401 | Temporarily wrong `$JIRA_TOKEN` | Symphony surfaces `{:error, {:jira_api_status, 401}}` clearly in logs; doesn't crash |
| Repo outside allowlist | Ticket referencing a non-allowlisted repo | Agent stops, posts Jira comment, exits cleanly |
| `gh push` denied | Temporarily set `OPENCLAW_GH_TOKEN` to a token without `contents:write` on the target repo | Push fails with clear error; no orphan PR; ticket isn't stuck "running" forever (Symphony's normal poll should re-evaluate) |
| Symphony killed mid-run | `kill -TERM $(pgrep -f symphony)` while agent is mid-implementation | On restart, ticket can be re-picked or marked for manual investigation; workspace doesn't corrupt |

Document the actual behavior of each in this file (append below) so future runs have ground truth.

---

## 7. How to stop / revert

### Graceful stop

```bash
kill -TERM $(pgrep -f 'symphony.*WORKFLOW')
# If unresponsive after ~10s:
kill -9 $(pgrep -f 'symphony.*WORKFLOW')
```

### Close an orphan PR

```bash
gh pr close --delete-branch <PR-number> --repo <owner>/<repo>
```

### Unfreeze a Jira ticket stuck in `agent:running`

Manually via the Jira UI: remove the label, set status back to whatever it was. There is no automated cleanup for this in Phase 1.

### Free disk space

Workspaces are at `~/symphony_workspaces/`. Each clone of a CiviCRM repo can be a few hundred MB. Manual cleanup:

```bash
# Anything older than 14 days, named like a Jira key:
find ~/symphony_workspaces -mindepth 1 -maxdepth 1 -type d -mtime +14 -name '[A-Z]*-*' -exec rm -rf {} +
```

---

## 8. Known issues

- **Dashboard label leak:** `SymphonyElixir.StatusDashboard` shows `Project: https://linear.app/project/<KEY>/issues` even when `tracker_kind=jira`. Cosmetic, doesn't affect functionality. Worth filing upstream against the fork once Phase 1 is stable.

- **Homebrew `symphony` binary does NOT include our Jira adapter.** The Homebrew tap (`sapsaldog/symphony`) ships a pre-compiled escript from upstream source — it has no `jira:` section detection and would silently fall back to the GitHub tracker (`ITS: github` in the dashboard, no tickets picked up). `start-symphony.sh` instead exec's `./elixir/bin/symphony`, a local escript built with our changes via `cd elixir && mise exec -- mix escript.build`. **Rebuild whenever you change Elixir code under `elixir/lib/`.** The wrapper refuses to start if the local binary is missing.
- **No rate-limit backoff specific to Jira 429:** The current Jira client logs and returns `{:error, {:jira_api_status, 429}}` but doesn't honor `Retry-After`. If the pilot triggers a lot of polling on a busy project, add `Retry-After` parsing to `Jira.Client` as a follow-up.
- **Fork is 10 commits behind `openai/main`:** rebase was attempted on 2026-05-13 and aborted due to conflicts in `orchestrator.ex`/`config.ex`/`agent_runner.ex`. Stayed on sapsaldog `HEAD` (commit `932e5f4`). If upstream gains a critical fix, plan ~1-2h to resolve the rebase manually.
- **Pre-existing flaky test:** `core_test.exs:454` fails ~5–10% of full-suite runs (`mix test`). Failure is not caused by this integration's changes (verified — none of the Jira files touch that path, and 10/10 isolated runs of `mix test test/symphony_elixir/jira/` pass). If `mix test` fails once, re-run before treating it as a regression.

- **Dev-site HTTP Basic Auth is operator-provided, not auto-discovered.** Compucorp dev/test sites sit behind a Traefik gateway with VAULT-encrypted Basic Auth (per-site, stored in `compucorp.sites.<host>.basic_auth`). The agent CANNOT decrypt VAULT values from Mongo in Phase 1 — operator must pass the credentials in via env vars (`DEV_SITE_BASIC_USER`/`DEV_SITE_BASIC_PASS`/`DEV_SITE_URL`) per site, or accept that the agent works from code-reading alone. Future work: expose a controlled decrypt endpoint, migrate basic_auth to a secrets manager the bot token can read, or replace Traefik basic auth with another gate (Cloudflare Access, ZeroTier IP allowlist) so the agent can reach dev sites without a human-in-the-loop step.

---

## 9. Running Symphony

Once the above is validated:

```bash
cd ~/projects/compuco-symphony
./start-symphony.sh
```

`Ctrl+C` to stop. Symphony does NOT have a daemon mode in Phase 1 — leave it in a foreground terminal.

**Never** invoke the `symphony` binary directly — only the wrapper, because that is where the env filtering and bot token swap happen (see §3.1, §3.2).

---

## 10. Troubleshooting

### "I added `agent:todo` and nothing happened after 30s"

In order of likelihood:

1. **Symphony isn't running.** It's a foreground process, not a daemon. Check `ps -ef | grep symphony | grep -v grep`. Start via `./start-symphony.sh`.

2. **Ticket status not in `active_states`.** The orchestrator filters the JQL results by status (in-memory). Open the ticket — is its status one of: `Backlog`, `To Do`, `Open`, `Reopened`, `Ready for Development`, `Ready for Dev`, `In Progress`? If it's in some custom status not listed, either move it to one of those, or add the custom status name to `tracker.active_states` in `WORKFLOW.md` and restart Symphony. (Project flows vary across Compucorp Jira projects.)

3. **Wrong label text.** Symphony looks for the literal label `agent:todo` (configurable via `jira.trigger_label` in WORKFLOW.md). `Agent:todo`, `agent-todo`, `agent_todo` won't match.

4. **JQL didn't return the ticket.** Replicate the JQL manually to diagnose:
   ```bash
   curl -sS -u "$JIRA_USER:$JIRA_TOKEN" -H "Accept: application/json" \
     -X POST -H "Content-Type: application/json" \
     "$JIRA_URL/rest/api/3/search/jql" \
     -d '{"jql":"labels = \"agent:todo\"","maxResults":10,"fields":["summary","status"]}' \
     | jq '.issues[] | {key, status: .fields.status.name, summary: .fields.summary}'
   ```
   If your ticket isn't here, the Jira side is the problem (label not actually applied, or `OpenClaw` doesn't have read access to that project).

5. **`OpenClaw` Jira account doesn't have read on the project.** If JQL returns nothing but you see the ticket in the UI, that's almost certainly it. Grant `OpenClaw` browse on the project, or use a different label-bearing ticket.

### "Symphony picks up the ticket but the agent does the wrong thing"

- Read the agent transcript (Symphony logs to stdout; pipe to a file when you start: `./start-symphony.sh 2>&1 | tee run-$(date +%Y%m%d-%H%M).log`).
- Check the workspace: `~/symphony_workspaces/<KEY>/` should contain `./.playbooks` symlink, `./plan.md` (if the agent reached the planning step), and `./repo/` (if it reached cloning).
- If the agent cloned the wrong repo, that's a prompt issue — update the WORKFLOW.md allowlist or the routing instructions.

### "The agent says it can't find a repo on the allowlist"

That's correct behavior. The current allowlist is `compucorp/ase` and `compucorp/compuclient`. If the ticket is for another repo:
- Add the repo to the allowlist in `WORKFLOW.md` (the prompt body section, under invariant 1).
- Add `openclawautomation` as collaborator with `write` on the repo.
- Validate via the curl loop in §3.2.
- Restart Symphony.

---

## 11. Analyzing what an agent did during a run

The Symphony `disk_log` only captures JSON-RPC notification types (`item/created`, `usage/update`) — not the content. To see **what the agent actually did** (which tools, which files, which skills, which bash commands), read the full Claude Code transcript:

```
~/.claude/projects/-Users-mar-symphony-workspaces-<JIRA-KEY>/<session-id>.jsonl
```

That JSONL has every tool_use block with its input, every text block the agent emitted, every Skill invocation. **It's the authoritative record.**

A helper script extracts a structured report:

```bash
cd ~/projects/compuco-symphony
./analyze-run.sh COMCL-1442
```

The report lists:
- All tool invocations (Bash, Read, WebFetch, Skill, etc.) with counts
- Which superpowers skills were invoked, flagging missing required ones
- Whether the `/review` slash command was actually run (vs the agent just describing review concepts)
- Every file read / written / edited
- Every bash command with its description
- Every WebFetch URL
- Atlassian MCP calls
- A sample of the agent's text output
- Whether Compucorp playbooks were read

Use it after every run to validate the agent actually followed WORKFLOW.md. Don't trust the Symphony disk_log alone — it doesn't carry enough.

**Multi-session caveat:** if Symphony got killed mid-run and the agent was restarted (or the ticket was re-dispatched), Claude Code creates a **new JSONL per session** in the same project dir. The script picks the most recent one. To inspect all sessions for a ticket:

```bash
ls -la ~/.claude/projects/-Users-mar-symphony-workspaces-<KEY>/
# pass each .jsonl to analyze-run.sh individually:
./analyze-run.sh ~/.claude/projects/-Users-mar-symphony-workspaces-<KEY>/<session-id>.jsonl
```

---

## 12. Reproducible test command

To re-verify the build and tests anytime:

```bash
cd ~/projects/compuco-symphony/elixir
mise exec -- mix compile --warnings-as-errors
mise exec -- mix test
mise exec -- mix format --check-formatted
```

All three must pass. As of handoff: 250 tests total (22 of which are Jira-specific: 8 tracker + 14 client). Note the pre-existing flake documented in §8.
