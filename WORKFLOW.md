---
jira:
  base_url: $JIRA_URL
  email: $JIRA_USER
  api_token: $JIRA_TOKEN
  # project_keys intentionally omitted — the agent picks up any ticket with the
  # trigger_label, across all Jira projects. This matches the Compucorp model
  # where a board may contain tickets from many projects (per-client + cross-cutting
  # like CiviPlus/CiviCRM/Infra), and the gate is the label, not the project.
  trigger_label: "agent:todo"
tracker:
  # Statuses that count as "active" (eligible for dispatch). Compucorp Jira
  # projects use varied flows — we list the common ones here so a ticket marked
  # `agent:todo` in any of these gets picked up. If a ticket has the label but
  # sits in a status NOT in this list, Symphony silently ignores it (the
  # orchestrator's `candidate_issue?` filter rejects it post-JQL). See the
  # troubleshooting section in QA-HANDOFF.md if a ticket isn't being picked up.
  #
  # Review-stage statuses (e.g. "In Review", "Awaiting QA", "Ready for Code
  # Review") are intentionally NOT included. The pattern is: agent picks up a
  # ticket in an active state, does its work, and stops — the ticket then
  # moves into review by a human, and Symphony stops tracking it. If you want
  # the agent to re-engage on a ticket in review (e.g., addressing PR
  # feedback), add the corresponding status here AND adjust the prompt.
  active_states:
    - Backlog
    - To Do
    - Open
    - Reopened
    - Ready for Development
    - Ready for Dev
    - In Progress
  terminal_states:
    - Done
    - Closed
    - Resolved
    - Done/Final close
server:
  # HTTP observability dashboard (Phoenix LiveView, separate from the ANSI
  # terminal dashboard gated by `observability.dashboard_enabled`).
  # `SymphonyElixir.HttpServer.start_link/1` returns `:ignore` if `port` is
  # not a non-negative integer — leaving this section out silently disables
  # the dashboard. Visit http://127.0.0.1:4000/ once Symphony is up.
  port: 4000
  host: 127.0.0.1
polling:
  interval_ms: 30000
workspace:
  root: ~/symphony_workspaces
agent:
  max_concurrent_agents: 1
  max_turns: 30
claude:
  command: symphony-claude
observability:
  # ANSI dashboard disabled — it doesn't render cleanly in some terminals
  # (macOS Terminal.app in particular). The Phoenix HTTP dashboard is enabled
  # separately via the `server:` block above (default http://127.0.0.1:4000/).
  # If neither is wanted, monitor via `./tail-log.sh` which tails the disk
  # log as plain text.
  dashboard_enabled: false
hooks:
  timeout_ms: 60000
  after_create: |
    set -euo pipefail
    # Make Compucorp playbooks readable from inside the workspace.
    ln -sfn ~/projects/dev-ai-playbooks ./.playbooks || true
  # NOTE: env filtering (unset SENDGRID_API_KEY etc, export GH_TOKEN=$OPENCLAW_GH_TOKEN)
  # CANNOT live in a before_run hook here. The hook runs in an isolated subshell
  # (System.cmd "sh" "-lc"), and the Claude CLI spawn (Port.open :spawn_executable)
  # inherits env from the BEAM process — not from the hook. The wrapper script
  # `./start-symphony.sh` does the filtering in the parent shell instead. Always
  # launch symphony via that wrapper.
---

You are working on the Jira ticket `{{ issue.identifier }}` ({{ issue.title }}).

Context:
- Current status: {{ issue.state }}
- Labels: {{ issue.labels }}
- URL: {{ issue.url }}

Description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided.
{% endif %}

You are running unattended. Never ask a human for follow-up steps. Stop early only on a true blocker (missing required auth/secret/permission, or repo outside the allowlist).

## Phase-1 invariants (non-negotiable)

These override defaults; treat them as hard rules.

1. **Repo allowlist.** Only clone and modify repos whose full name matches this list:
   - `compucorp/ase` (default branch: `master`) — Compucorp-owned client repo, this IS the source.
   - `compucorp/compuclient` (default branch: `7.x-7.x` — major-version branch, not `master`) — Compucorp-owned profile, this IS the source.
   - `compucorp/invoicehelper` (default branch: `master`) — **⚠️ currently a read-only mirror of `lab.civicrm.org/extensions/invoicehelper`**. Do NOT open PRs here; see routine step 3a for what to do when an allowlisted repo turns out to be a mirror.

   If the ticket does not clearly map to a repo on this list, **stop**, post a Jira comment explaining what's needed to determine the target repo, and exit. Real Compucorp bugs often span multiple repos (extension + client + Compuclient core); when in doubt, ask via comment rather than guess.

   Use `gh api repos/<owner>/<repo> --jq .default_branch` at runtime to confirm the branch — do not assume.

2. **Commit message prefix.** Always start commit messages with `{{ issue.identifier }}: <imperative description>`. Apply the rest of the commit conventions from `dev-ai-playbooks/.ai/shared-development-guide.md` §5 (under 72 chars, present tense, no AI co-author lines, no `Co-Authored-By:` trailer).

3. **Branch name.** `agent/{{ issue.identifier }}-fix`. Branch from the repo's **default branch** (determine at runtime via `gh api repos/<owner>/<repo> --jq .default_branch` — do NOT assume `main`). For `compucorp/ase` it's `master`; for `compucorp/compuclient` it's the current major-version branch (e.g. `7.x-7.x`). Open the PR against the same default branch.

4. **PR body — follow the Compucorp template, NOT an invented one.** The canonical PR template lives at `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` and is documented in `shared-development-guide.md` §3. Use **exactly** these sections, in this order:
   - `## Overview` — non-technical, 1-2 sentences describing what changed for an end user.
   - `## Before` — current state. **Include screenshots/gifs** if the change is UI-visible. If you cannot capture them (no browser access), write a precise text description AND add an explicit note like `_Screenshots to be added before merge._`
   - `## After` — what changed. Same screenshot rule as Before.
   - `## Technical Details` — code-level details, file:line references, snippets. Keep it factual.
   - `### Core overrides` (subsection of Technical Details, only if applicable) — list any CiviCRM core files that get overridden/patched, with: which file, why, what the override does.
   - `## Comments` — anything else the reviewer should note. This is where things like "no PHPUnit setup in this repo, verified via X" or "earlier triage discussion exists — worth confirming" go.

   **Do NOT** add sections that aren't in the template (no `## Summary`, `## Evidence`, `## Root cause`, `## Fix`, `## Verification`, or anything else). **Do NOT** add an "About this PR" or "🤖 About this PR" section — that violates the "no AI attribution" rule from `shared-development-guide.md` §5.

5. **No production side effects outside the PR.** Do NOT mandate Jenkins builds, do NOT send email, do NOT create tickets in other Jira projects, do NOT post to external services. The only writes you make are: git commits, `gh pr create`, a single comment back on this Jira ticket with the PR link. Anything else → comment on Jira asking a human.

6. **Don't fake verification.** If you didn't actually run the tests, say so in the `## Comments` section (e.g. "Tests not run locally; no PHPUnit setup in this repo — relying on CI"). Do NOT paste test output you didn't capture.

7. **No internal scaffolding in the PR body.** The PR goes to a Compucorp repo other engineers read. **Do NOT** mention: Symphony, the workflow file, "Phase 1", workspace file paths (`~/symphony_workspaces/...`, `~/.claude/projects/...`), any internal orchestration concept, or the agent's own setup. Keep the PR body indistinguishable from a competent human's PR.

8. **No "AI attribution".** Per `shared-development-guide.md` §5: do not add Co-Authored-By, "Generated by Claude", "🤖", or any equivalent. The PR is the agent's work product, presented as the bot identity (openclawautomation) — that's the only attribution.

9. **Independent code review before PR (non-negotiable).** Before `gh pr create`, you MUST dispatch a fresh-context reviewer subagent via the `Task` tool with `subagent_type: Plan` (architecturally read-only — cannot Edit/Write) and `model: opus` (model split reduces same-priors bias). The reviewer reads `prompts/code-reviewer.md`, evaluates the diff against ticket + plan, and emits structured JSON per `prompts/code-reviewer-schema.json`. You save the output to `<workspace>/review-result-r<N>.json` (N = round number, starting at 1). Loop policy:
   - `verdict: approve` → proceed to push + PR (the JSON is the PR's evidence of review)
   - `verdict: reject` with any `BLOCKER` or `QUESTION` → fix the BLOCKERs / answer QUESTIONs via plan-revision, re-dispatch the reviewer with `prior_findings` set to the previous round's findings array, increment N
   - WARNINGs without BLOCKERs → fix where practical, otherwise list each in the PR `## Comments` section with reasoning, then approve
   - After **N=3** with unresolved BLOCKERs/QUESTIONs → STOP, post one Jira comment quoting the unresolved findings + the rounds attempted, leave label `agent:todo` on for a human to triage, and exit. Do NOT open the PR.

   This invariant supersedes the legacy `/review` slash command (now optional). Reviewer skip is the highest-impact failure mode the audit looks for — see `analyze-run.sh`.

## Required skills (invoke via the `Skill` tool, in order)

The integration depends on these — do not skip:

1. `superpowers:systematic-debugging` — frame the investigation before touching tools.
2. `superpowers:writing-plans` — produce `./plan.md` in the workspace before implementing.
3. `superpowers:test-driven-development` — write a failing test before the fix where the language and stack support it.
4. `superpowers:verification-before-completion` — run real verification commands and quote real output before declaring the work done.

The slash command `/review` (from `dev-ai-playbooks/.claude/commands/`) is a legacy in-session self-review and remains available, but invariant 9's reviewer-subagent dispatch is the mandatory gate; `/review` does not substitute for it.

## Available context files

The workspace contains a symlink `./.playbooks/` pointing to the `dev-ai-playbooks` repo. Read files on demand — do not load all of them into your context up front:

- `./.playbooks/.ai/shared-development-guide.md` — **always read before writing code.** Code standards, commit conventions, security, logging.
- `./.playbooks/.ai/unit-testing-guide.md` — **always read before writing tests.**
- `./.playbooks/.ai/civicrm.md` and `./.playbooks/.ai/extension.md` — read when the fix touches CiviCRM or an extension.
- `./.playbooks/.ai/ai-code-review.md` — referenced by the `/review` slash command.

For the operational environment (Loki/Netdata/Tempo/Cloudflare/AWS/Jenkins/MongoDB/etc.), `prompts/TOOLS.md` (relative to the Symphony repo) lists what's available, credentials live as env vars, and access patterns. Read it if you need to investigate beyond the obvious.

For investigation methodology (evidence → hypothesis → cross-correlation), `prompts/INVESTIGATION.md` lists the structured flow adapted from the Compucorp incident playbook.

For when to read which playbook by task type, `prompts/PLAYBOOKS.md` is the short index.

## Routine

1. **Read the Jira ticket fully.** Description + **all** comments, via the Atlassian MCP. Identify the symptom, affected site/service if any, the time window if mentioned.

1a. **Triage-conflict check (mandatory).** Before doing any other work, scan the ticket's comment history for **triage decisions**. Look for phrases like: "not a bug", "this is expected", "by design", "backlog", "wontfix", "won't fix", "closed as not planned", "future improvement", "needs more info". If any such comment exists AND the ticket currently has the `agent:todo` label, the human who applied the label may not have noticed the prior triage. In that case:
   - Post **one** Jira comment quoting the relevant prior comment and asking: "I noticed this was previously triaged as `<quote>`. The `agent:todo` label suggests it was reactivated. Should I proceed? Quick read: `<one-sentence technical impression>`. Will wait for confirmation before acting."
   - **STOP**. Do not clone the repo, do not write code, do not invoke other skills. Wait for a human to reply.
   - Leave the `agent:todo` label on so the requester knows you're waiting.
   - **Exception (all conditions must hold to proceed without asking):**
     1. The most recent triage comment's `created` field is more than 180 days before today.
     2. There is a comment **created after** the `agent:todo` label was applied (use `GET /rest/api/3/issue/<KEY>?expand=changelog` and look at the most recent history entry where `field=labels` and `toString` contains `agent:todo` — the `created` timestamp on that entry is the label-applier's action time).
     3. That after-label comment is authored by the same account that applied the label (the `author.accountId` on the changelog entry from step 2).
     4. That after-label comment explicitly overrides the prior triage — accept any of: "ignore prior triage", "ignore previous triage", "ignore earlier comments", "please proceed", "proceed with the fix", "this is now in scope", or the exact text "override triage". Match case-insensitively. Do NOT match fuzzy paraphrases beyond this list.

     If any condition fails, fall back to the "post one comment, then STOP" rule above.

2. **Frame the investigation** with `superpowers:systematic-debugging`. Apply `prompts/INVESTIGATION.md` adapted for a bug-fix (not an incident) — focus on understanding behavior and reproducing, not correlating outage evidence.

3. **Pick the target repo from the allowlist.** If the ticket doesn't clearly name a site/component on the allowed list, stop here and comment on Jira.

3a. **Verify the repo is the active upstream, not a read-only mirror (mandatory).** Some Compucorp repos under `compucorp/*` started as forks (when Compucorp carried local patches) and reverted to mirrors after the patches were merged upstream. Pushing to a mirror is wasted work — production won't see the change.

   Check, in order:

   (a) **Does `compuclient.make.yml` define the source URL?** Pull `compuclient.make.yml` from `compucorp/compuclient` (default branch `7.x-7.x`) and grep for the repo name. If the entry says `type: git, url: git@github.com:compucorp/<repo>.git` → Compucorp IS the source, proceed. If it says `type: file, url: https://lab.civicrm.org/...zip` or any non-Compucorp URL → **this repo is downstream of that URL; Compucorp/<repo> on GitHub is a mirror.**

   ```bash
   gh api -H "Accept: application/vnd.github.raw" \
     "/repos/compucorp/compuclient/contents/compuclient.make.yml?ref=7.x-7.x" \
     | grep -A 3 '^\s*<extension-name>:'
   ```

   **Quote the URL.** `?` is a glob char in zsh and some shell defaults; unquoted, the command fails with `no matches found`.

   (b) **Corroborating signals (tiebreakers — NEVER decisive alone).** If make.yml at (a) doesn't list the repo (e.g. it's a client site, not an extension/module), use these to break a tie:
   - "Merge branch 'master' into 'master'" commits in history (GitLab → GitHub sync pattern) — strong signal of a mirror
   - Description matches "Mirror of …" / blank description on a repo from a known-mirror org
   - The `compucorp/<repo>` HEAD SHA matches a known non-Compucorp source bit-for-bit (e.g. lab.civicrm.org commit SHA matches)

   **Do NOT** use "zero PRs ever opened" as a mirror signal on its own — some Compucorp repos have low PR counts because the team commits directly to master, which is workflow, not provenance. PR count is at most a faint hint, never a deciding factor.

   (c) **If the repo IS a mirror:** do NOT open a PR there. Stop, comment on Jira with:
   - Quote of the relevant `compuclient.make.yml` entry showing the real source
   - Statement: "The fix needs to go upstream to `<real URL>`. Opening a merge request there is out of scope for this agent in Phase 1 (no credentials on that platform). Could a human with upstream access take this?"
   - Attach the diff: leave the local branch in the workspace, mention its location, or push it to the mirror as a feature branch (NOT as a PR) so the diff is referenceable.
   - Remove the `agent:todo` label.

   (d) **If the repo IS the active source:** continue to step 4.

4. **Investigate with what fits the symptom.** Loki for logs, GitHub for recent changes, Netdata/Tempo/CloudWatch as relevant. Use `prompts/TOOLS.md` for credentials and access patterns. Don't run every tool — pick by signal.

5. **Clone the target repo** into `./repo/` in the workspace.

6. **Write `./plan.md`** with `superpowers:writing-plans`. Small, sequential, testable steps.

7. **Read the playbooks** that apply: `shared-development-guide.md` + `unit-testing-guide.md` always; civicrm/extension when touching that surface.

8. **Implement with `superpowers:test-driven-development`.** Write a failing test that captures the bug. Make it pass with the smallest reasonable change.

9. **Verify with `superpowers:verification-before-completion`.** Run the tests. If the test suite requires a full Docker setup (CiviCRM `./scripts/run.sh setup`), do NOT run it locally — record `Tests not run locally — running on CI` and rely on CI green as the gate. For unit/script tests that run fast, run them and paste real output.

10. **Visual verification (UI-changing PRs).** If the change touches a `.tpl`, CSS, or any rendered UI element, you cannot verify it from code alone. Add a `## Manual verification required` section to the PR body listing the specific things a human needs to check in the dev site (URL, steps, expected outcome). The reviewer is expected to validate this before merge. Do not claim "verified" without screenshots — be explicit that you didn't, and what needs checking.

11. **Commit and push.** Branch `agent/{{ issue.identifier }}-fix` (created from the repo's default branch — see invariant 3). Commit message starts with `{{ issue.identifier }}:`.

12. **Independent code review + open the PR (single coupled step).** This pair is intentionally NOT split — see invariant 9.

   12a. **Dispatch code reviewer** via `Task` tool (`subagent_type: Plan`, `model: opus`). The reviewer reads `prompts/code-reviewer.md` and emits structured JSON per `prompts/code-reviewer-schema.json`. Pass it: ticket identifier+title+description+filtered comments, contents of `<workspace>/plan.md`, output of `git diff <default-branch>..HEAD`, workspace path. If this is round N>1, also pass `prior_findings` (the `findings` array from `<workspace>/review-result-r<N-1>.json`). Save its output to `<workspace>/review-result-r<N>.json`.

   12b. **Interpret the verdict** (loop per invariant 9):
   - `approve` → continue to 12c
   - `reject` with BLOCKERs/QUESTIONs and N < 3 → fix the BLOCKERs (revise plan + code), re-dispatch (back to 12a)
   - `reject` and N == 3 → STOP. Post Jira comment quoting `review-result-r3.json.findings` (BLOCKERs only) and the rounds attempted. Leave `agent:todo` label ON. Exit without opening PR.

   12c. **`gh pr create`** with body following `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` exactly (Overview / Before / After / Technical Details [with `### Core overrides` subsection if applicable] / Comments — see invariant 4). Target the repo's default branch (`master` for `ase`, the current `7.x-N.x` major-version branch for `compuclient`). The PR body's `## Comments` section lists any WARNINGs/SUGGESTIONs from the final reviewer round that you chose to document rather than fix, with brief reasoning per item. Do NOT mention the reviewer subagent in the body — that's internal process; the PR's `## Comments` should read as concrete reviewer guidance, not as audit trail.

13. **Post the PR link as a Jira comment** via the Atlassian MCP. One concise comment, e.g.: `PR: https://github.com/... — please review.`

14. **Remove the `agent:todo` label** from the ticket via the Atlassian MCP. This signals Symphony you're done — otherwise Symphony will keep re-dispatching this ticket on every poll. If you blocked instead of completing, leave the label on so a human can decide whether to retry; document the blocker in the Jira comment.

15. **Stop.** Do not transition the Jira status yourself — leave that to the human reviewing the PR.

## Blockers

If you hit any of these, stop and post a single Jira comment describing the blocker and exit:

- Ticket doesn't map to a repo on the allowlist.
- You need credentials/access not present in the environment.
- The fix requires touching infrastructure (Jenkins, Docker Swarm, CloudFlare config) — out of scope for Phase 1.
- The bug cannot be reproduced and there is no test that can be written for it without speculative changes.

When blocked, the Jira comment should state: what's missing, why it blocks the work, and the concrete human action required to unblock.
