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
  max_concurrent_agents: 2
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
    # Co-locate repro_helpers.py with where repro.py will live, so `python3 repro.py`
    # finds it via Python's default sys.path[0] = script's parent directory.
    ln -sfn ~/projects/compuco-symphony/prompts/repro_helpers.py ./repro_helpers.py || true
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

1. **Repo allowlist.** Only clone and modify repos whose full name matches this list (derived from what the `openclawautomation` GitHub user has push access to in the `compucorp` org as of 2026-05-18):

   **Annotated — known quirks; read the note before patching:**
   - `compucorp/ase` (default branch: `master`) — Compucorp-owned client repo, this IS the source.
   - `compucorp/compuclient` (default branch: `7.x-7.x` — major-version branch, not `master`) — Compucorp-owned profile, this IS the source.
   - `compucorp/invoicehelper` (default branch: `master`) — **⚠️ currently a read-only mirror of `lab.civicrm.org/extensions/invoicehelper`**. Do NOT open PRs here; see routine step 3a for what to do when an allowlisted repo turns out to be a mirror.
   - `compucorp/ies` — Compucorp-owned client site (IES2). Determine the default branch at runtime via `gh api repos/compucorp/ies --jq .default_branch`.
   - `compucorp/civicrm-core` — **⚠️ fork of `civicrm/civicrm-core`**. Run routine step 3a's `compuclient.make.yml` check before assuming Compucorp is the source.
   - `compucorp/uk.co.vedaconsulting.mosaico` — **⚠️ fork**. Same caveat: run step 3a.
   - `compucorp/webform_civicrm` — **⚠️ fork**. Same caveat: run step 3a.
   - `compucorp/nes-mirror` — **⚠️ mirror (name says so)**. Do NOT open PRs here without confirming upstream.

   **Default branch for unannotated entries below:** discover at runtime via `gh api repos/<owner>/<repo> --jq .default_branch`. Do not assume `master`/`main`. (Repeated on each section heading below for agents that jump straight to a section.)

   **Client sites / themes / distributions** (default branch: discover at runtime via `gh api`):
   - `compucorp/civiplus-distribution`
   - `compucorp/ciwem`
   - `compucorp/core-website`
   - `compucorp/cst`
   - `compucorp/drw-website`
   - `compucorp/dta`
   - `compucorp/eseb`
   - `compucorp/hse_dais_documents`
   - `compucorp/hse_dais_main_app`
   - `compucorp/hse_dais_merge`
   - `compucorp/irs`
   - `compucorp/mm`
   - `compucorp/tcos`

   **Extensions / modules / themes** (default branch: discover at runtime via `gh api`):
   - `compucorp/abn`
   - `compucorp/compu_bs5`
   - `compucorp/compuco_civicrm_commerce`
   - `compucorp/drupal-sso`
   - `compucorp/FDW`
   - `compucorp/io.compuco.gocardless`
   - `compucorp/io.compuco.impactstack`
   - `compucorp/io.compuco.lmsd2lintegration`
   - `compucorp/io.compuco.paymentprocessingcore`
   - `compucorp/payments-middleware`
   - `compucorp/ssp_bootstrap`
   - `compucorp/ssp_core`
   - `compucorp/uk.co.compucorp.civiawards`
   - `compucorp/webform_bootstrap`

   **Infrastructure / tooling / Docker images / configs** (default branch: discover at runtime via `gh api`; ⚠️ patching these affects fleet-wide deploys — apply extra scrutiny, prefer to escalate to a human unless the fix is purely cosmetic / docs):
   - `compucorp/anondbs-list`
   - `compucorp/ansible.inventory`
   - `compucorp/compuco.docker.images.ansible`
   - `compucorp/compuco.docker.images.php-fpm`
   - `compucorp/compuco.docker.images.php-fpm.civicrm`
   - `compucorp/compudeploy`
   - `compucorp/docker.application-profiles`
   - `compucorp/homebrew-tools`
   - `compucorp/infrastructure.docker-mcp`
   - `compucorp/infrastructure.varnish-ecs`
   - `compucorp/jenkins`
   - `compucorp/jenkins-configurations`
   - `compucorp/mysql-database-anonymizer`
   - `compucorp/openclaw-configurations`
   - `compucorp/terraform`

   If the ticket does not clearly map to a repo on this list, **stop**, post a Jira comment explaining what's needed to determine the target repo, and exit. Real Compucorp bugs often span multiple repos (extension + client + Compuclient core); when in doubt, ask via comment rather than guess.

   Use `gh api repos/<owner>/<repo> --jq .default_branch` at runtime to confirm the branch — do not assume.

2. **Commit message prefix.** Always start commit messages with `{{ issue.identifier }}: <imperative description>`. Apply the rest of the commit conventions from `dev-ai-playbooks/.ai/shared-development-guide.md` §5 (under 72 chars, present tense, no AI co-author lines, no `Co-Authored-By:` trailer).

3. **Branch name.** `agent/{{ issue.identifier }}-fix`. Branch from `BASE_COMMIT` as resolved in Routine step 3b — this equals the default branch tip when the deployed site is current, or an older commit when the site is behind. Always open the PR **against the repo default branch** regardless of where you branched from. Determine the default branch at runtime via `gh api repos/<owner>/<repo> --jq .default_branch` — do NOT assume `main`. For `compucorp/ase` it's `master`; for `compucorp/compuclient` it's the current major-version branch (e.g. `7.x-7.x`).

4. **PR body — follow the Compucorp template, NOT an invented one.** The canonical PR template lives at `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` and is documented in `shared-development-guide.md` §3. Use **exactly** these sections, in this order:
   - `## Overview` — non-technical, 1-2 sentences describing what changed for an end user.
   - `## Before` — current state. **Include screenshots/gifs** if the change is UI-visible. If you cannot capture them (no browser access), write a precise text description AND add an explicit note like `_Screenshots to be added before merge._`
   - `## After` — what changed. Same screenshot rule as Before.
   - `## Technical Details` — code-level details, file:line references, snippets. Keep it factual.
   - `### Core overrides` (subsection of Technical Details, only if applicable) — list any CiviCRM core files that get overridden/patched, with: which file, why, what the override does.
   - `## Comments` — anything else the reviewer should note. This is where things like "no PHPUnit setup in this repo, verified via X" or "earlier triage discussion exists — worth confirming" go.

   **Do NOT** add sections that aren't in the template (no `## Summary`, `## Evidence`, `## Root cause`, `## Fix`, `## Verification`, or anything else). **Do NOT** add an "About this PR" or "🤖 About this PR" section — that violates the "no AI attribution" rule from `shared-development-guide.md` §5.

5. **No production side effects outside the PR — narrow Jenkins carve-out.** Do NOT send email, do NOT create tickets in other Jira projects, do NOT post to external services. The only writes you make are: git commits, `gh pr create`, a single comment back on this Jira ticket with the PR link, AND **at most two** Jenkins build triggers per run — one for each of these exact job paths (in order):

       `/job/Deployments/job/Dev%20Sites%20-%20Compucontainer/job/Create%20Dev%20Site%20-%20Client%20Specific`
       `/job/Deployments/job/Dev%20Sites%20-%20Compucontainer/job/_Release%20Dev%20Site`

   Both triggers fire from step 12b-bis (post-reviewer-approval, pre-`gh pr create`), using `trigger_dev_site` (Phase A) and `trigger_release_devsite` (Phase B) helpers in `prompts/repro_helpers.py`, and are bounded to `SITE_DEPLOYABLE_REPOS` (defined below). Valid Jenkins POST counts per run: **0** (skipped), **1** (Phase A completed, Phase B skipped), **2** (full success). Counts `> 2` are a workflow violation. **No other Jenkins write paths are permitted**: not other jobs, not DELETE/PUT/PATCH on these jobs, not parameter edits, not job/folder mutations. The audit (`analyze-run.py:detect_jenkins_writes`) greps for the literal job-path substrings above — keep them in sync. Anything else Jenkins-related → comment on Jira asking a human.

   **`SITE_DEPLOYABLE_REPOS`** — only these repos can be deployed by the carved-out job (the rest of the push allowlist in invariant #1 stays unchanged; the dev-site step skips with a `## Comments` note for non-site repos):
   ```
   ase, ies, eseb, ciwem, dta, drw-website, tcos, irs,
   hse_dais_documents, hse_dais_main_app, hse_dais_merge,
   civiplus-distribution, core-website, mm, cst
   ```
   `compuclient` is intentionally excluded: it's a Drupal install profile consumed by the sites above via `compuclient.make.yml`, not a standalone-deployable site. A Phase 1.5 design pass would add it if a real Jira ticket needs the dev-site step against it.

6. **Don't fake verification.** If you didn't actually run the tests, say so in the `## Comments` section (e.g. "Tests not run locally; no PHPUnit setup in this repo — relying on CI"). Do NOT paste test output you didn't capture.

7. **No internal scaffolding in the PR body.** The PR goes to a Compucorp repo other engineers read. **Do NOT** mention: Symphony, the workflow file, "Phase 1", workspace file paths (`~/symphony_workspaces/...`, `~/.claude/projects/...`), any internal orchestration concept, or the agent's own setup. Keep the PR body indistinguishable from a competent human's PR.

8. **No "AI attribution".** Per `shared-development-guide.md` §5: do not add Co-Authored-By, "Generated by Claude", "🤖", or any equivalent. The PR is the agent's work product, presented as the bot identity (openclawautomation) — that's the only attribution.

9. **Independent code review before PR (non-negotiable).** Before `gh pr create`, you MUST dispatch a fresh-context reviewer subagent via the `Task` tool with `subagent_type: Plan` (architecturally read-only — cannot Edit/Write) and `model: opus` (model split reduces same-priors bias). The reviewer reads `prompts/code-reviewer.md`, evaluates the diff against ticket + plan, and emits structured JSON per `prompts/code-reviewer-schema.json`. You save the output to `<workspace>/review-result-r<N>.json` (N = round number, starting at 1). Loop policy:
   - `verdict: approve` → proceed to push + PR (the JSON is the PR's evidence of review)
   - `verdict: reject` with any `BLOCKER` or `QUESTION` → fix the BLOCKERs / answer QUESTIONs via plan-revision, re-dispatch the reviewer with `prior_findings` set to the previous round's findings array, increment N
   - WARNINGs without BLOCKERs → fix where practical, otherwise list each in the PR `## Comments` section with reasoning, then approve
   - After **N=3** with unresolved BLOCKERs/QUESTIONs → STOP, post one Jira comment quoting the unresolved findings + the rounds attempted, leave label `agent:todo` on for a human to triage, and exit. Do NOT open the PR.

   This invariant supersedes the legacy `/review` slash command (now optional). Reviewer skip is the highest-impact failure mode the audit looks for — see `analyze-run.sh`.

10. **PII redaction when citing external observability data.** Several read-only credentials (SendGrid Mail Activity, MongoDB `compucorp.sites`, Loki stack logs) return responses that contain **end-user PII** — recipient email addresses, full names, sometimes contact comments. The full JSONL transcript of your run is persisted by the audit (`analyze-run.sh`) and visible to operators reviewing the run, and anything you paste into a PR description or Jira comment is permanent. When citing evidence from these sources: **redact recipient emails** (`r***@example.com`), do NOT paste contact names verbatim, do NOT include subject lines or message bodies. Quote only the structural evidence (timestamps, status codes, IDs) that supports the fix. See `prompts/TOOLS.md` §SendGrid for the canonical redaction pattern.

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

## DRY-RUN OVERRIDE

**Activation condition:** This block applies ONLY when `{{ issue.labels }}` contains `agent:dry-run`. If the current ticket does NOT have that label, skip this entire section and follow the normal Routine.

When active, this is a **dry-run** for end-to-end validation. Execute the Routine normally **through step 12b-bis (dev-site deploy + after.png)**, then **STOP**. Specifically:

- Do steps 1–11 fully (investigate, plan, implement, commit locally).
- Do step 12a (dispatch the reviewer subagent and save `review-result-r<N>.json`) — we want to validate the reviewer path works.
- Do step 12b-bis **fully (both Phase A and Phase B)** if the repo is in `SITE_DEPLOYABLE_REPOS` — we want to validate the entire two-phase dev-site path. **Pass `lifespan=1` to Phase A** (`trigger_dev_site`) so dry-run sites evaporate fast and don't accumulate. Phase B (`trigger_release_devsite`) has no lifespan parameter and always runs to completion. This means dry-run produces **two** Jenkins triggers (count=2 in the audit) and both `before.png` and `after.png` should land in `.agent-artifacts/`. The Jenkins triggers are real production side effects, but the user opted into autonomous dev-site provisioning; dry-run E2E validation has no value if we skip the new step.
- **Do NOT run `gh pr create`** (skip 12c entirely). No PR is to be opened.
- **Suppress ALL agent-initiated Jira writes in dry-run mode** — no comments, label mutations, status transitions, worklogs, or issue links of any kind, with ONE exception (the success-path label removal below). This is a categorical rule, not an enumeration: any new agent-initiated Jira write added to the Routine in future MUST also be suppressed under dry-run. The currently-known write paths covered: step 13 PR-link comment, step 1a triage-conflict comment, Blockers-section block comment, invariant 1's allowlist-miss comment, step 3b's multi-site / zero-site / ambiguous-images.php / failed-rev-parse comments, any TOOLS.md gated-request comment (e.g. Loki production approval, AWS role-ARN request), and any future comment-on-exit path. The operator triggered the dry-run, has the workspace + `<workspace>/dry-run-summary.md` + `<workspace>/AGENT_DONE` as the complete audit trail, and can inspect everything directly. Jira viewers (other engineers, clients) MUST NOT see test-run artifacts.
- **Label handling depends on outcome:**
  - **Dry-run SUCCESS** (reviewer approved at any round): remove BOTH `agent:todo` AND `agent:dry-run` labels via the Atlassian MCP. **This is the only Jira mutation permitted in dry-run mode**, and exists to prevent the post-completion retry storm.
  - **Dry-run BLOCKED** (reviewer rejected at N=3, or any other blocker per step 12b / the Blockers section): leave BOTH labels ON for human triage. The block reason goes into `<workspace>/dry-run-summary.md` ONLY — NOT into a Jira comment. If the operator wants to share the block reason on Jira, they post it manually after reviewing the workspace.
- Leave the local branch + commits in the workspace `./repo/` for human inspection.
- At the end, write `<workspace>/dry-run-summary.md` containing: (a) target repo + branch, (b) files changed (output of `git diff --stat <default-branch>..HEAD`), (c) reviewer verdict and rounds attempted, (d) what step 12c onwards *would* have done, (e) any caveats or unverified claims.
  - (f) Visual-repro outcome — one of:
    - `committed-repro` (script ran, assertion fired, before.png at <workspace>/before.png)
    - `gate-skipped` (gate condition failed; reason)
    - `assertion-failed` (script ran but assert_bug_reproduced didn't fire)
    - `host-not-allowlisted` (assert_staging_host refused)
  - (g) **If the run was blocked (any path — reviewer N=3, Blockers section, triage-conflict, allowlist-miss, multi/zero-site match, ambiguous deploy ref, etc.)**, include the full text that WOULD have been the Jira comment under normal mode. Structure it with explicit subheadings so the operator can copy-paste verbatim into Jira if they choose:
    - `### Block reason` — one-line summary
    - `### Investigation summary` — what the agent looked at and what it found
    - `### Unblocker actions` — concrete next steps a human would need to take (env vars to set, screenshots to attach, decisions to make)
    `dry-run-summary.md` is the operator's audit trail; nothing escapes to Jira.
- Write `<workspace>/AGENT_DONE` with content: `dry-run <ISO-8601-timestamp> {{ issue.identifier }}`

Invariants 1–11 still apply in full. The only thing being skipped is the external side-effect emission.

## Routine

0. **Sentinel check (belt-and-suspenders).** If `<workspace>/AGENT_DONE` already exists, output one line: "Run already completed (`<content of AGENT_DONE>`). Exiting." and stop immediately without reading the ticket or doing anything else. The orchestrator-level preflight should have prevented this dispatch; this step guards the rare race where the file was written between the preflight check and this turn starting.

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

3b. **Identify the deployed git ref and branch from it, not from `master`/the repo default branch (mandatory).** The repo's default branch reflects ongoing development; the **affected site is running a specific tag/commit** that may be behind, ahead, or on a divergent patch branch. Investigating, reproducing, and patching against the wrong ref produces fixes that target lines that don't exist on the live site, or "re-fix" bugs already fixed upstream-of-deploy.

   Steps:

   (a) **Identify the affected site hostname.** Look for a URL like `<sitename>.cc-staging.site`, `*.civiplus.net`, or a custom domain in the ticket description, comments, or screenshot alt-text. If no URL is named:
   - Query Mongo by repo: `db.sites.find({reporitory: /<repo-name>/i}, {_id: 1, swarm_cluster: 1})` (note the canonical typo `reporitory`). Use the repo name from step 3 as the search term.
   - If exactly one site matches, use it and note the inference in `## Comments`.
   - If multiple sites match (e.g. staging + live + data), ask via Jira comment listing them and STOP.
   - If zero match, ask via Jira comment and STOP.

   (b) **Query Mongo for the deployed Docker image tag.** Projection: `db.sites.find_one({"_id": "<hostname>"}, {"images": 1})`. The `images.php` field is authoritative (it ships the application code). Extract the ref as the portion after the last `:`, e.g. `compucorp/ies_php:7.x-4.4-patch.1--3rc5` → ref `7.x-4.4-patch.1--3rc5`.

   Guard: if the `images.php` value has no `:`, or the tag portion is `latest` or empty, the deploy pipeline didn't embed a git ref — post a Jira comment quoting the raw `images.php` value and STOP. A human must identify the correct ref before the agent can safely patch.

   (c) **Resolve the ref to a commit.** Do this after cloning the repo (step 5) so the tags are available:

   ```bash
   # Ensure full history — guards against shallow clones
   git fetch --unshallow --tags 2>/dev/null || git fetch --all --tags --quiet
   REF="7.x-4.4-patch.1--3rc5"  # from Mongo
   BASE_COMMIT=$(git rev-parse "${REF}^{commit}" 2>/dev/null)  # ^{commit} unwraps annotated tags
   DEFAULT=$(gh api "repos/<owner>/<repo>" --jq .default_branch)
   DEFAULT_COMMIT=$(git rev-parse "$DEFAULT")
   ```

   If `git rev-parse` fails (tag or branch not found in repo): post a Jira comment quoting the Mongo `images.php` value and the failed lookup, and STOP — the deploy pipeline may have tagged under a different name or the ref was force-deleted.

   (d) **Pick the branch base:**
   - `BASE_COMMIT == DEFAULT_COMMIT`: deployed == default branch tip. Branch from default as usual. Note in PR `## Comments`: "Confirmed deployed ref `<REF>` resolves to the same commit as `<default-branch>`."
   - `BASE_COMMIT` is an ancestor of `DEFAULT_COMMIT` (site is behind): Branch from `BASE_COMMIT` — `git checkout -b agent/<KEY>-fix "$BASE_COMMIT"`. PR target is still the repo default. Document in `## Comments`: "Site is deployed at `<REF>`, which is N commits behind `<default-branch>`. Branched from the deployed commit; merging will require a rebase/forward-port — include `git log --oneline BASE_COMMIT..<default>` in the Jira comment so the reviewer can assess conflicts."
   - Divergent (neither is ancestor of the other): branch from `BASE_COMMIT` and **STOP, comment on Jira** asking where the fix should land (deployed patch branch, default, or both). Include `git log --oneline BASE_COMMIT..<default> | head -20` and the reverse in the comment so the reviewer can see the divergence. This is a release-management decision, not a code decision.

   (e) Throughout investigation (steps 4–10), all `git log`, `git blame`, line-number references, and code reads must use `BASE_COMMIT` as the reference point — not the default branch tip.

4. **Investigate with what fits the symptom.** Loki for logs, GitHub for recent changes, Netdata/Tempo/CloudWatch as relevant. Use `prompts/TOOLS.md` for credentials and access patterns. Don't run every tool — pick by signal.

5. **Clone the target repo** into `./repo/` in the workspace.

6. **Write `./plan.md`** with `superpowers:writing-plans`. Small, sequential, testable steps.

7. **Read the playbooks** that apply: `shared-development-guide.md` + `unit-testing-guide.md` always; civicrm/extension when touching that surface.

8. **Implement with `superpowers:test-driven-development`.** Write a failing test that captures the bug. Make it pass with the smallest reasonable change.

   **Build-artifact policy (theme repos).** If you edit `*.scss`/`*.sass` in a theme directory that has a build script (`package.json` with a `"build"` script, or `gulpfile.js`), also regenerate the corresponding compiled CSS (`dist/css/*.css` or equivalent) and commit it in the same fix commit. `compucorp/ase` and `compucorp/ies` both serve the committed compiled CSS directly via theme `.info` declarations and do NOT rebuild in CI — an SCSS-source-only PR deploys with no styling change. If the local build produces output incompatible with the committed format (e.g., minified vs expanded), hand-append the compiled rules in the expected format and document this in PR `## Comments`. See `code-reviewer.md` "Build-artifact policy" section for the reviewer-side invariant.

9. **Verify with `superpowers:verification-before-completion`.** Run the tests. If the test suite requires a full Docker setup (CiviCRM `./scripts/run.sh setup`), do NOT run it locally — record `Tests not run locally — running on CI` and rely on CI green as the gate. For unit/script tests that run fast, run them and paste real output.

10. **Visual verification (any ticket with an observable symptom).** Apply the two-condition gate from `prompts/visual-repro.md` § 1: (a) a specific staging URL is resolvable from the ticket (description, comments, or via step 3b Mongo lookup), AND (b) the URL passes `assert_staging_host`. The gate is no longer restricted to UI-changing diffs — backend tickets (PHP/hook/API/queue/email fixes) reproduce through the UI surface where their symptom appears (Civi admin view, `/admin/reports/dblog`, Mautic preview, SearchKit, etc.). If either condition fails, document the gate decision in PR `## Comments` (one line: "Visual repro skipped: <reason>") and proceed to step 11 with `## Manual verification required` in the PR body.

    When both conditions hold:

    10a. Read `prompts/visual-repro.md`.
    10b. Pick the simplest pattern (1/2/3) that fits the bug; copy the skeleton to `<workspace>/repro.py` (workspace root — NOT inside `./repo/`).
    10c. Fill `reproduce(page)` and `assert_bug_reproduced(page)`. First line of `main()` must be `pathlib.Path("before.png").unlink(missing_ok=True)`. If the substantive diff is CSS-only per `prompts/visual-repro.md` §8's gate, ALSO add the after-state pass — see §8 for the code fixture, the inject-after-reproduce ordering, and the `assert_bug_fixed` contract.
    10d. Run: `cd <workspace> && python3 repro.py`. Outputs `<workspace>/before.png` on success (and `<workspace>/after.png` when §8 applies). (The `cd` is required because `page.screenshot(path="...")` is cwd-relative.)
    10e. If exit 0 AND `before.png` exists: copy `repro.py`, `before.png`, and `after.png` (if present) into the client repo on the agent branch:

    ```bash
    cd <workspace>/repo
    mkdir -p .agent-artifacts/{{ issue.identifier }}/
    cp ../before.png .agent-artifacts/{{ issue.identifier }}/before.png
    if [ -f ../after.png ]; then
      cp ../after.png .agent-artifacts/{{ issue.identifier }}/after.png
    fi
    git add .agent-artifacts/{{ issue.identifier }}/
    git commit -m "{{ issue.identifier }}: add visual reproduction evidence"
    ```

    Only the screenshots are committed. The reproduction script and its helpers stay in `<workspace>/` for the operator's audit (and persist in Claude Code's per-session JSONL transcript at `~/.claude/projects/`). They are operator-internal tooling, not artifacts the client repo's maintainers need.

    Then PR `## Before` reads (markdown image syntax with the agent-branch raw URL — `<owner>/<repo>` from step 3):

    ```markdown
    ![Before — <one-line bug summary>](https://github.com/<owner>/<repo>/raw/agent/{{ issue.identifier }}-fix/.agent-artifacts/{{ issue.identifier }}/before.png)

    Reproduction captured against `<staging URL>` (use the `SITE` constant from `repro.py`, e.g. `https://ies2.cc-staging.site`) via a Playwright assertion that fired before the screenshot was taken.
    ```

    If `after.png` was also captured (CSS-only diff per §8), PR `## After` reads:

    ```markdown
    ![After — <one-line description of the fix>](https://github.com/<owner>/<repo>/raw/agent/{{ issue.identifier }}-fix/.agent-artifacts/{{ issue.identifier }}/after.png)

    Captured by injecting the compiled equivalent of the SCSS change via `page.add_style_tag()` on the same staging URL — the fix is not yet deployed; injection simulates the post-deploy CSS state. The inverse assertion (`assert_bug_fixed`) fired before screenshot.
    ```

    If `after.png` was NOT captured (diff includes JS/PHP/behavior files), PR `## After` uses the manual-verification block from `visual-repro.md` §8.

    If exit non-zero OR `before.png` missing: PR body gets `## Manual verification required` with explicit reproduction steps (URL, preconditions, what to look for).

    10f. **Artifact lifecycle note (operator-facing):** artifacts land in master after PR merge (~1–2 MB per UI ticket; doubled when §8 captures `after.png`). The branch-name raw URL works during PR review and breaks after branch deletion; artifacts remain in master's git history at the merge commit indefinitely. This is intentional for v1.6 — the GitHub user-attachments CDN requires `user_session` cookie auth (cli/cli#13256, community#29993) and is not accessible to the bot PAT, so asymmetric storage would require manual per-PR upload. Object storage (S3, Cloudflare R2) is the cleaner alternative for v2 if repo bloat becomes material.

11. **Commit and push.** Branch `agent/{{ issue.identifier }}-fix` (created from `BASE_COMMIT` per invariant 3 and step 3b). Commit message starts with `{{ issue.identifier }}:`.

12. **Independent code review + open the PR (single coupled step).** This pair is intentionally NOT split — see invariant 9.

   12a. **Dispatch code reviewer** via `Task` tool (`subagent_type: Plan`, `model: opus`). The reviewer reads `prompts/code-reviewer.md` and emits structured JSON per `prompts/code-reviewer-schema.json`. Pass it: ticket identifier+title+description+filtered comments, contents of `<workspace>/plan.md`, output of `git diff <default-branch>..HEAD`, workspace path. If this is round N>1, also pass `prior_findings` (the `findings` array from `<workspace>/review-result-r<N-1>.json`). Save its output to `<workspace>/review-result-r<N>.json`.

   12b. **Interpret the verdict** (loop per invariant 9):
   - `approve` → continue to 12c
   - `reject` with BLOCKERs/QUESTIONs and N < 3 → fix the BLOCKERs (revise plan + code), re-dispatch (back to 12a)
   - `reject` and N == 3 → STOP. Post Jira comment quoting `review-result-r3.json.findings` (BLOCKERs only) and the rounds attempted. Leave `agent:todo` label ON. Write `<workspace>/AGENT_DONE` with content: `blocked-review <ISO-8601-timestamp> {{ issue.identifier }}`. Exit without opening PR.

   12b-bis. **Two-phase dev-site verification** (mandatory if the target repo is in `SITE_DEPLOYABLE_REPOS` per invariant #5; skip with a one-line `## Comments` note otherwise — e.g. extension, theme, or infra repo). Runs ONLY after 12b returned `verdict: approve`; the reviewer evaluates code, the dev site evaluates the deployed result.

   **Skip guard — doc-only diffs:** Run `git diff --stat <default-branch>..HEAD` and if every changed path matches `*.md`, `*.txt`, or is comment-only, skip 12b-bis with `## Comments` note: "Dev-site step skipped: doc-only diff." Saves Jenkins on trivial PRs.

   Symphony's stall detector fires after ~5 min of no Claude API activity. Jenkins builds can run for up to 30 min. **Never run a poll loop in a single blocking Bash call.** The pattern below uses 90-second timeout chunks — each iteration is a fresh Bash call that resets the stall timer. Cap all poll loops at 20 re-runs (~30 min); treat cap as a failure per the failure table below.

   ---

   ### Phase A — Create dev site at broken tag (before.png)

   **Goal:** deploy the code that was live when the bug was filed, loaded with the best available database, so `assert_bug_reproduced` fires on the dev site and we can capture `before.png` on identical infrastructure to Phase B.

   **A1. Resolve the broken tag.**
   Use `BASE_COMMIT` already in scope from step 3b (resolved from Mongo `sites.images.php` when the staging site was first inspected). Do **not** re-query Mongo — staging may have been re-deployed since.

   **A2. Pick the database source** based on where the bug was reported:
   - **Bug reported on a staging site** (hostname ends `.cc-staging.site`): pass the bare staging hostname as `anondb_url` (e.g. `ies2.cc-staging.site`). The Groovy pipeline generates S3 presigned URLs for that hostname's backup bucket automatically.
   - **Bug reported on production or any other site**: call `resolve_anondb_url("<ticket-hostname-from-3b>")` from `repro_helpers` — returns the client-matched anondbs URL (e.g. `https://anondbs.cc-infra.tools/dir.php?name=…`). If it returns `None` → skip both phases entirely, note in `## Comments`: "Dev-site step skipped: anondb lookup returned None for `<hostname>`." Continue to 12c.

   **A3. Push the broken tag and trigger Phase A (fast, one-shot):**
   ```bash
   # In the workspace repo directory — create a Docker-safe tag at BASE_COMMIT
   git tag agent-{{ issue.identifier }}-before <BASE_COMMIT>
   git push origin agent-{{ issue.identifier }}-before
   ```
   ```python
   from repro_helpers import trigger_dev_site, devsite_git_tag
   import pathlib
   before_tag = devsite_git_tag(f"agent/{{ issue.identifier }}-before")
   queue_url = trigger_dev_site(
       git_repo  = f"git@github.com:compucorp/{repo}.git",
       git_tag   = before_tag,
       anondb_url= anondb,          # bare hostname or anondbs URL from A2
       public    = False,
       lifespan  = (1 if dry_run else None),
   )
   pathlib.Path("<workspace>/.devsite-queue").write_text(queue_url)
   print(f"triggered Phase A: {queue_url}")
   ```
   This returns in < 5 s.

   **A4. Poll loop (each iteration is a fresh Bash call):**

   Before each iteration: if `<workspace>/.devsite-host` already exists, skip to A5 (restart-safe).
   ```python
   from repro_helpers import poll_until_deployed, wait_until_site_up
   import pathlib, sys

   queue_url = pathlib.Path("<workspace>/.devsite-queue").read_text().strip()

   # Stall-detector restart safety: Jenkins purges the queue item ~5 min
   # after the build starts. On re-invocations, pass the cached build_url
   # to skip Phase 1 queue polling (avoids a 404 RuntimeError).
   build_url_file = pathlib.Path("<workspace>/.devsite-build-url")
   build_url = build_url_file.read_text().strip() if build_url_file.exists() else None

   host = poll_until_deployed(queue_url, build_url=build_url,
                              expect_public=False,
                              timeout_s=90, raise_on_timeout=False)
   if host is None:
       # Cache the build_url for future iterations (if Phase 1 resolved it
       # this iteration, the build_url is in .devsite-build-url if we wrote
       # it — but poll_until_deployed doesn't expose it directly).
       # Workaround: query the queue item once to get executable.url.
       import requests, os
       try:
           r = requests.get(f"{queue_url}api/json",
                            auth=(os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"]),
                            timeout=15)
           if r.status_code == 200:
               exe = r.json().get("executable") or {}
               if exe.get("url"):
                   build_url_file.write_text(exe["url"])
       except Exception:
           pass
       print("Phase A still building — re-run to continue polling")
       sys.exit(42)   # sentinel: agent re-runs after 60 s
   wait_until_site_up(host, timeout_s=900)
   pathlib.Path("<workspace>/.devsite-host").write_text(host)
   print(f"PHASE_A_HOST={host}")
   ```
   Exit code `42` → sleep 60 s, re-run. `0` → proceed to A5. Any other non-zero or cap exceeded → **Phase A failure** (see failure table; skip both phases, continue to 12c).

   **A5. Capture before.png:**

   Run `visual-repro.md §9a`: reproduce the bug → `assert_bug_reproduced` → capture `before.png`. Save to `<workspace>/before.png` and copy to `repo/.agent-artifacts/{{ issue.identifier }}/before.png`. Commit on the agent branch (second commit after the fix commit — intentional append post-approval).

   If `assert_bug_reproduced` does **not** fire (file-system-only or SSP-specific bug that has no observable surface on the dev site): log a warning, fall back to the staging `before.png` already captured in step 3b, continue to Phase B regardless.

   **A6. Clean up the before tag:**
   ```bash
   git push origin --delete agent-{{ issue.identifier }}-before
   ```

   ---

   ### Phase B — Release fix branch to same dev site (after.png)

   **Goal:** deploy the agent's fix branch to the same dev site (same data, no DB reimport) and assert the bug is gone.

   **B1. Push the fix tag and trigger Phase B (fast, one-shot):**
   ```bash
   git tag agent-{{ issue.identifier }}-fix HEAD
   git push origin agent-{{ issue.identifier }}-fix
   ```
   ```python
   from repro_helpers import trigger_release_devsite, devsite_git_tag
   import pathlib
   fix_tag   = devsite_git_tag(f"agent/{{ issue.identifier }}-fix")
   devsite_host = pathlib.Path("<workspace>/.devsite-host").read_text().strip()
   queue_url = trigger_release_devsite(
       site_url = devsite_host,
       git_tag  = fix_tag,
   )
   pathlib.Path("<workspace>/.release-queue").write_text(queue_url)
   print(f"triggered Phase B: {queue_url}")
   ```

   **B2. Poll loop (each iteration is a fresh Bash call):**

   Before each iteration: if `<workspace>/.release-done` already exists, skip to B3 (restart-safe).
   ```python
   from repro_helpers import poll_until_released, wait_until_site_up
   import pathlib, sys
   queue_url    = pathlib.Path("<workspace>/.release-queue").read_text().strip()
   devsite_host = pathlib.Path("<workspace>/.devsite-host").read_text().strip()
   result = poll_until_released(queue_url, site_url=devsite_host,
                                timeout_s=90, raise_on_timeout=False)
   if result is None:
       print("Phase B still running — re-run to continue polling")
       sys.exit(42)
   wait_until_site_up(devsite_host, timeout_s=300)
   pathlib.Path("<workspace>/.release-done").write_text("ok")
   print(f"PHASE_B_DONE host={devsite_host}")
   ```
   Exit code `42` → sleep 60 s, re-run. `0` → proceed to B3. Any other non-zero or cap exceeded → **Phase B failure** (see failure table; skip `after.png`, continue to 12c).

   **B3. Capture after.png:**

   Run `visual-repro.md §9b`: `assert_bug_fixed` → capture `after.png`. Save to `<workspace>/after.png` and copy to `repo/.agent-artifacts/{{ issue.identifier }}/after.png`. Commit on the agent branch.

   If `assert_bug_fixed` **fails** (assertion didn't fire): **BLOCK** the PR. Post a Jira blocker comment quoting (a) the reviewer's approval, (b) both Jenkins build numbers + dev-site URL, (c) the assertion failure, and (d) likely cause: "DB or data state may not reproduce the bug on the dev site." Leave `agent:todo` ON. Write `<workspace>/AGENT_DONE` with prefix `blocked-verify`. Skip 12c entirely.

   **B4. Clean up the fix tag:**
   ```bash
   git push origin --delete agent-{{ issue.identifier }}-fix
   ```

   ---

   ### Failure modes

   | Failure | Behaviour |
   |---|---|
   | Repo not in `SITE_DEPLOYABLE_REPOS` | Skip both phases. One-line `## Comments` note. Continue to 12c. |
   | Doc-only diff | Skip both phases. One-line `## Comments` note. Continue to 12c. |
   | anondb lookup returns `None` | Skip both phases. Note in `## Comments`. Continue to 12c. |
   | Phase A FAILURE / timeout / cap | Skip both phases. Note build # in `## Comments`. Use staging `before.png` from step 3b. Continue to 12c. |
   | `assert_bug_reproduced` doesn't fire | Log warning. Use staging `before.png`. Continue to Phase B. |
   | Phase B FAILURE / timeout / cap | Skip `after.png`. Note build # in `## Comments`. Continue to 12c. |
   | `assert_bug_fixed` fails on dev site | **Block PR.** `AGENT_DONE` with `blocked-verify` prefix. Jira blocker comment. |

   **Orphan-tag note:** A6 and B4 push-delete the Jenkins tags after each phase. If the agent crashes or is interrupted between the tag push and the delete, `agent-<TICKET>-before` and/or `agent-<TICKET>-fix` tags will leak on the remote. They are harmless (lightweight tags; no CI triggers on them) but accumulate over time. If you notice orphan `agent-*` tags when inspecting a repo, delete them manually with `git push origin --delete <tag-name>`.

   ### PR-body additions (12c)

   - `## Before` — references dev-site `before.png` if captured; otherwise staging `before.png` from step 3b.
   - `## After` — `after.png` if captured, plus: `Live verification at https://<host> (auto-expires <date>).`
   - `## Comments` — one line per job: "Phase A: Jenkins build #N, tag=`<before_tag>`, anondb=`<url>`." and "Phase B: Jenkins build #N, tag=`<fix_tag>`."

   When 12b-bis runs successfully, `visual-repro.md` §8's inject-based `after.png` path is **superseded** — do not run it. The §8 path only fires when 12b-bis was skipped AND the diff is CSS-only.

   12c. **`gh pr create`** — Only after 12a was dispatched AND 12b returned `verdict: approve` on the latest round AND (12b-bis ran to completion OR 12b-bis was skipped per its own gates — but NEVER if 12b-bis blocked). Never run `gh pr create` directly without those rounds having been the final actions; running it bypasses the invariant #9 gate. The audit (`analyze-run.sh`) reports the reviewer-dispatch count and the `gh pr create` count separately — an operator inspecting the run will see immediately if the latter happened without the former and treat that as a workflow violation. Body follows `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` exactly (Overview / Before / After / Technical Details [with `### Core overrides` subsection if applicable] / Comments — see invariant 4). Target the repo's default branch (`master` for `ase`, the current `7.x-N.x` major-version branch for `compuclient`). The PR body's `## Comments` section lists any WARNINGs/SUGGESTIONs from the final reviewer round that you chose to document rather than fix, with brief reasoning per item. Do NOT mention the reviewer subagent in the body — that's internal process; the PR's `## Comments` should read as concrete reviewer guidance, not as audit trail.

13. **Post the PR link as a Jira comment** via the Atlassian MCP. One concise comment, e.g.: `PR: https://github.com/... — please review.`

14. **Remove the `agent:todo` label** from the ticket via the Atlassian MCP. This signals Symphony you're done — otherwise Symphony will keep re-dispatching this ticket on every poll. If you blocked instead of completing, leave the label on so a human can decide whether to retry; document the blocker in the Jira comment.

15. **Write `AGENT_DONE` and stop.** Create `<workspace>/AGENT_DONE` with content: `success <ISO-8601-timestamp> {{ issue.identifier }}`. Do not transition the Jira status yourself — leave that to the human reviewing the PR.

## Blockers

If you hit any of these, stop and post a single Jira comment describing the blocker and exit:

- Ticket doesn't map to a repo on the allowlist.
- You need credentials/access not present in the environment.
- The fix requires touching infrastructure (Jenkins, Docker Swarm, CloudFlare config) — out of scope for Phase 1.
- The bug cannot be reproduced and there is no test that can be written for it without speculative changes.

When blocked, the Jira comment should state: what's missing, why it blocks the work, and the concrete human action required to unblock. After posting the comment, write `<workspace>/AGENT_DONE` with content: `blocked <ISO-8601-timestamp> {{ issue.identifier }}` and exit.

## AGENT_DONE schema

`AGENT_DONE` is a single-line sentinel file with exactly three space-separated fields and exactly one of four allowed prefixes:

```
<prefix> <ISO-8601-timestamp> <issue.identifier>
```

| Prefix | Meaning | Written by |
|---|---|---|
| `success` | Routine ran to completion, PR opened, Jira commented, label removed. | Step 15 |
| `dry-run` | DRY-RUN OVERRIDE ran through step 12a, reviewer approved, no external side effects. | DRY-RUN OVERRIDE block |
| `blocked-review` | Reviewer subagent rejected at N=3 (invariant #9 loop limit). | Step 12b |
| `blocked-verify` | Reviewer approved, dev-site deploy succeeded, but `assert_bug_fixed` did not fire — typically the fresh anondb lacks the data state that triggers the bug. Operator decides whether to seed data + retry, push the PR manually after sanity-checking the dev site, or widen anondb selection. | Step 12b-bis |
| `blocked` | Generic blocker (Blockers section: repo not on allowlist, missing credentials, infra-touching scope, irreproducible bug). | Blockers section |

Any other prefix, missing fields, malformed timestamp, or mismatched `issue.identifier` is a workflow bug and must be flagged by `analyze-run.py`. Operators rely on these strings to triage runs at a glance; do not invent new prefixes without updating this schema first.
