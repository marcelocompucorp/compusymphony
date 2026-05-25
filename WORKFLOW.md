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
   - `compucorp/nes-mirror` — **⚠️ mirror (name says so)**. Do NOT open PRs here without confirming the mirror's source (run step 3a).

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

       `/job/Test_Jobs/job/Create%20Dev%20Site%20-%20Client%20Specific%20-%20Pipeline%20Test`  _(temporary — Groovy pipeline test job; flip to Deployments/… when promoted to prod)_
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

11. **No ticket narrative in source.** In-source free-form comments (block or line) explain non-obvious behaviour the next reader of the code needs — a Drupal 7 quirk, a hook-ordering constraint, why a guard exists. They do NOT recount ticket history, cascade rationale, reviewer feedback, or the agent's reasoning process — that belongs in the commit message and PR description. If a comment starts with the ticket ID, paraphrases the PR `## Cause` section, or restates "what the user reported" — delete it. This rule does NOT apply to docblocks (PHPDoc `@param`/`@return`, JSDoc, etc.) — those remain required where the linter or convention demands them. (Recurring reviewer feedback from human engineers — most recently Ayush on `compucorp/ies#232`.)

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

- Do steps 1–11 fully (investigate, plan, implement, commit locally). **For dual-target runs: step 11a's `git push <client-remote> qa-<TICKET>` IS a real remote side effect and IS permitted in dry-run** — the `qa-<TICKET>` branch push is a prerequisite for Phase B and is functionally equivalent to the Jenkins triggers (opt-in real side effect for E2E validation). Suppress only `gh pr create` and Jira writes. The operator can manually delete the `qa-<TICKET>` branch after the dry-run if desired.
- Do step 12a (dispatch the reviewer subagent and save `review-result-r<N>.json`) — we want to validate the reviewer path works.
- Do step 12b-bis **fully (both Phase A and Phase B)** if the repo is in `SITE_DEPLOYABLE_REPOS` — we want to validate the entire two-phase dev-site path. **Pass `lifespan=1` to Phase A** (`trigger_dev_site`) so dry-run sites evaporate fast and don't accumulate. Phase B (`trigger_release_devsite`) has no lifespan parameter and always runs to completion. This means dry-run produces **two** Jenkins triggers (count=2 in the audit) and both `before.png` and `after.png` should land in `.agent-artifacts/`. The Jenkins triggers are real production side effects, but the user opted into autonomous dev-site provisioning; dry-run E2E validation has no value if we skip the new step.
- **Do NOT run `gh pr create`** (skip 12c entirely). No PR is to be opened.
- **Suppress ALL agent-initiated Jira writes in dry-run mode** — no comments, label mutations, status transitions, worklogs, or issue links of any kind, with ONE exception (the success-path label removal below). This is a categorical rule, not an enumeration: any new agent-initiated Jira write added to the Routine in future MUST also be suppressed under dry-run. The currently-known write paths covered: step 13 PR-link comment, step 1a triage-conflict comment, Blockers-section block comment, invariant 1's allowlist-miss comment, step 3b's multi-site / zero-site / ambiguous-images.php / failed-rev-parse comments, step 4a data-state block comments (production host, missing/ambiguous DB, entity misconfigured), any TOOLS.md gated-request comment (e.g. Loki production approval, AWS role-ARN request), attachment uploads (the prerequisite upload step for screenshot-embedding comments is also a Jira write and must be suppressed in dry-run mode — skip both the upload and the wiki-markup comment, falling back to a plain text-only MCP comment if a Jira write is permitted, or omitting the comment entirely if not), and any future comment-on-exit path. The operator triggered the dry-run, has the workspace + `<workspace>/dry-run-summary.md` + `<workspace>/AGENT_DONE` as the complete audit trail, and can inspect everything directly. Jira viewers (other engineers, clients) MUST NOT see test-run artifacts.
- **Label handling depends on outcome:**
  - **Dry-run SUCCESS** (reviewer approved at any round): remove BOTH `agent:todo` AND `agent:dry-run` labels via the Atlassian MCP. **This is the only Jira mutation permitted in dry-run mode**, and exists to prevent the post-completion retry storm.
  - **Dry-run BLOCKED** (reviewer rejected at N=3, or any other blocker per step 12b / the Blockers section): leave BOTH labels ON for human triage. The block reason goes into `<workspace>/dry-run-summary.md` ONLY — NOT into a Jira comment. If the operator wants to share the block reason on Jira, they post it manually after reviewing the workspace.
- Leave the local branch + commits in the workspace `./repo-client/` (and `./repo-core/` if dual-target) for human inspection.
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

## Screenshot embedding in Jira comments

**When to apply:** Any time the agent posts a block/failure Jira comment OR the step 13 PR-link comment, and a screenshot file is available (`before.png`, `after.png`, or any workspace PNG), use the two-step REST API workflow below to embed it inline. If no screenshot is available, post the comment normally via the Atlassian MCP as usual.

**Why not MCP:** The Atlassian MCP `addCommentToJiraIssue` tool uses ADF format and cannot embed inline images from attachments. Screenshot embedding requires two direct REST API calls.

**Step 1 — Upload the screenshot as a Jira attachment:**
```bash
curl -s -X POST \
  -H "Authorization: Basic $(echo -n "$JIRA_USER:$JIRA_TOKEN" | base64)" \
  -H "X-Atlassian-Token: no-check" \
  -F "file=@/path/to/screenshot.png;type=image/png" \
  "${JIRA_URL%/}/rest/api/3/issue/<KEY>/attachments"
```
Parse the `filename` field from the **first element** of the JSON array response — use this confirmed filename, not the input filename (Jira may rename duplicates on re-upload). _Jira renames collision filenames to `name-N.ext` (e.g. `before-1.png`), which is URL-safe. If your source file has spaces or special characters in its name, rename it to a safe filename (e.g. `before.png`) before uploading._

**Step 2 — Post the comment via the REST API v2 endpoint** (NOT the MCP, NOT v3 ADF) using wiki markup:
```bash
curl -s -X POST \
  -H "Authorization: Basic $(echo -n "$JIRA_USER:$JIRA_TOKEN" | base64)" \
  -H "Content-Type: application/json" \
  "${JIRA_URL%/}/rest/api/2/issue/<KEY>/comment" \
  -d '{"body": "...wiki markup text...\n\n!<confirmed_filename>|width=800!\n\n...more text..."}'
```
Use `!<confirmed_filename>|width=800!` to embed the image inline. The `v2` endpoint accepts wiki markup (plain text with `!filename|width=N!` directives); the `v3` endpoint only accepts ADF and cannot render inline attachment images.

**Credentials:** Use `$JIRA_URL` for the base URL (strip trailing slash with `${JIRA_URL%/}`), `$JIRA_USER` and `$JIRA_TOKEN` for Basic auth — same credentials used throughout.

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

3. **Pick the target repo(s) from the allowlist** based on the bug's ROOT CAUSE location.

   **Authoritative references (Compucorp Confluence):**
   - [Compuclient folder structure (modules)](https://compucorp.atlassian.net/wiki/spaces/SD/pages/1519091822/)
   - [Compuclient folder structure (themes & general)](https://compucorp.atlassian.net/wiki/spaces/SD/pages/84344881/)

   The mapping table in step 3.2 below is derived from these pages. If a path doesn't fit any row, consult the wiki and post a Jira comment if the answer remains ambiguous.

   **Step 3.1 — preliminary classification from the ticket alone.** Use the ticket's project prefix as the FIRST hint (`IESBUILD-*` → `ies`, `MMMM-*` → `mm`, etc.). This identifies the originating **client repo**. It does NOT yet determine whether the fix lives in the client or in the core repo.

   **Step 3.2 — refine after preliminary investigation (steps 4–6).** Once you've cloned the client repo and looked at the symptom, classify the bug:

   - **Client-exclusive** — root cause is in `sites/all/themes/custom/<site>/`, `sites/all/modules/{custom,features}/<site>/`, or another path that does NOT exist in other clients. Target: ONLY the client repo. Proceed with the standard single-repo flow (steps 5 onwards use `./repo-client/` only).

   - **Core-rooted** — root cause (or DOM-element lifecycle owner) is under `profiles/compuclient/...`, OR in any Compucorp-maintained module/extension that ships into multiple clients. Direct edits to `profiles/compuclient/...` inside a client repo are **ephemeral** (overwritten on the next Compuclient profile upgrade), so they must NOT be the fix. Two targets are required:
     - **Primary (PR target):** the corresponding core repo. Decide by root-cause path location, not by symptom:

       | Path | Target repo |
       |---|---|
       | `sites/all/...` | **Client-exclusive** — fix in the client repo only (no core PR) |
       | `profiles/compuclient/modules/contrib/<name>/` | Core repo `compucorp/<name>` |
       | `profiles/compuclient/themes/contrib/<name>/` | Core repo `compucorp/<name>` |
       | Anywhere else under `profiles/compuclient/...` | Core repo `compucorp/compuclient` |

       **Drupal-name-to-repo-name caveat:** module directory names may use underscores (`core_website`) while the GitHub repo uses a hyphen (`core-website`). The find-by-file approach in step 11's `git apply --directory` derivation handles this without a translation table.

       **Lifecycle exception (universal — applies even when ADDING new behaviour):** even if you are not modifying an existing file in `profiles/compuclient/...`, the fix is **core-rooted** whenever the DOM element's interactive behaviour lifecycle (markup, `Drupal.behaviors`, `form_alter`, preprocess, `hook_node_view`, popup toggle in a template `.inc`, etc.) is owned by code under `profiles/compuclient/...`. Writing a new per-site behaviour to patch around a broken core lifecycle is the IESBUILD-247 anti-pattern. Cross-reference: step 7a "Coordinator behavior gate".

       **No-behaviour escape clause:** if the element is rendered by core (markup lives in `profiles/compuclient/...`) but has **no core interactive behaviour at all** (no `Drupal.behaviors`, no inline JS in the template, no event binding anywhere under `profiles/compuclient/...`), treat as **Uncertain** and ask via Jira comment before classifying. Prevents over-classifying purely static markup as core-rooted when a per-site enhancement is genuinely the right scope.

       **Unknown module fallback (`compuco_projects.yml`):** if a path matches row 2 or 3 but the module name is not on the static allowlist:
       ```bash
       gh api "repos/compucorp/compuclient/contents/compuco_projects.yml" \
         --jq '.content' | base64 -d | grep -i "<name>"
       ```
       If found → proceed with the core PR to `compucorp/<name>`. If not found → STOP and ask. Closes the gap between the ~28-repo static allowlist and the ~54-repo authoritative list.
     - **Secondary (QA-branch target):** the originating client repo (from step 3.1). Branch name `qa-<ticket-key>` (e.g., `qa-IESBUILD-247`). NO PR is opened on the secondary; only a branch push for QA-team testing.

   - **Uncertain** — root cause is not clearly client-exclusive or core-rooted after investigation. **STOP. Do NOT guess.** Post a Jira comment explaining what you found, why the classification is ambiguous, and what additional information would resolve it. Proceed to step 15 with prefix `blocked`. A misclassification that opens a PR in the wrong repo is harder to undo than a stopped run.

   Use `gh api "repos/<owner>/<repo>" --jq .default_branch` at runtime to confirm the default branch for each target repo.

3a. **Verify the repo is the active core repo, not a read-only mirror (mandatory).** Some Compucorp repos under `compucorp/*` started as forks (when Compucorp carried local patches) and reverted to mirrors after the patches were merged upstream. Pushing to a mirror is wasted work — production won't see the change.

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
   - Statement: "The fix needs to be made in the core repo at `<real URL>`. Opening a merge request there is out of scope for this agent in Phase 1 (no credentials on that platform). Could a human with write access to that repo take this?"
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

4a. **Data-state verification (when symptom is state-dependent).** Apply this step when the symptom is "entity not found / not displayed / condition not met / element missing" AND either (a) a generic page load does not reproduce it, OR (b) the bug description names a specific entity or record. If neither condition holds, skip to step 5.

   **Resolve the RDS environment from the deployed hostname (step 3b's result):**
   - `*.cc-staging.site` → use `$RDS_STAGING_*` env vars + `$RDS_JUMP_HOST_MAIN`
   - `*.cc-test.site` or dev sites → use `$RDS_DEV_*` env vars + `$RDS_JUMP_HOST_MAIN`
   - `*.civiplus.net` → use `$RDS_CIVIPLUS_*` + `$RDS_JUMP_HOST_CIVIPLUS` (only some clients have RDS access — if the DB is missing, post a Jira comment noting the gap and **STOP**)
   - **Production or unknown host → STOP.** Post a Jira comment noting no RDS credentials exist for production hosts. Proceed to step 15 with prefix `blocked-data`.

   **Look up the DB name from MongoDB** (key field: `env_vars.DRUPAL_DB_NAME`):
   ```python
   from pymongo import MongoClient; import os
   client = MongoClient(f"mongodb://{os.environ['MONGO_USER']}:{os.environ['MONGO_PASSWORD']}@{os.environ['MONGO_HOST']}:{os.environ['MONGO_PORT']}/?authSource={os.environ['MONGO_AUTH_SOURCE']}")
   site = client['compucorp']['sites'].find_one({'_id': '<hostname>'}, {'env_vars.DRUPAL_DB_NAME': 1})
   db_name = site['env_vars']['DRUPAL_DB_NAME']
   ```
   If `DRUPAL_DB_NAME` is missing or starts with `VAULT:` (encrypted), fall back to `SHOW DATABASES LIKE '<site-slug>%'` on the RDS instance and pick the `_drupal` database. If the result is ambiguous, post a Jira comment and **STOP**.

   **Open an SSH tunnel** — see `prompts/TOOLS.md §RDS` for the full command. Kill any stale tunnel first (`pkill -f "ssh -f -N -L.*$RDS_STAGING_LOCAL_PORT"`), then:
   ```bash
   ssh -f -N -L "$RDS_STAGING_LOCAL_PORT:$RDS_STAGING_ENDPOINT:3306" "$RDS_JUMP_HOST_MAIN" -o StrictHostKeyChecking=accept-new
   ```
   Adjust the env-var prefix for DEV or CIVIPLUS environments. `StrictHostKeyChecking=accept-new` is required — without it the agent blocks on a host-key prompt. **If the tunnel fails to establish** (connection refused on `127.0.0.1:<port>` after one retry), post a Jira comment quoting the SSH error and proceed to step 15 with prefix `blocked-data`.

   **Query with MySQL — SELECT only.** The credentials are read-only; do not attempt `UPDATE`/`INSERT`/`DELETE`/`ALTER`. Redact PII (names, emails, contact details) when quoting results into Jira comments or PR bodies — same rule as invariant 10. If a query returns `ERROR 1146 (Table doesn't exist)`, do NOT assume entity data is correct — the table schema may differ between Drupal versions (D7: `field_data_field_*`, D10: `node__field_*`). Try the alternative schema form; if still failing, STOP, post a Jira comment noting the schema mismatch, and proceed to step 15 with prefix `blocked-data`.

   **Branch on findings:**
   - **Entity data correct** (images set, published, dates eligible, expected rows present) → code bug confirmed; continue to step 5.
   - **Entity misconfigured** (NULL image FID, unpublished, date constraint not met, record absent) → post a Jira comment quoting structural evidence (IDs, NULL columns, counts — not raw user data), and proceed to step 15 with prefix `blocked-data`.

5. **Clone the target repo(s).**

   - **Client-exclusive bugs (single-target):** clone the client repo into `./repo-client/` in the workspace. Do **not** create `./repo-core/`. Subsequent steps that reference `./repo-core/` apply only to dual-target runs (each such step is explicitly scoped). The run is single-target whenever `./repo-core/` is absent from the workspace.

   - **Core-rooted bugs (dual-target):** clone BOTH and track two base commits:
     - `./repo-core/` ← `compucorp/<core-repo>` (the PR target). Base commit is called **`BASE_COMMIT_CORE`** — the core repo's default branch tip (discovered via `gh api "repos/compucorp/<core-repo>" --jq .default_branch`). RC-branch targeting is deferred to a future Symphony version; if a Compuclient release is mid-flight, the operator opens the core PR manually against the RC branch.
     - `./repo-client/` ← `compucorp/<client-repo>` (the QA-branch target). Base commit is called **`BASE_COMMIT_CLIENT`** — the client's deployed tag resolved by step 3b Mongo lookup, same as for single-target runs.

     Step 3b's Mongo lookup applies to `./repo-client/` only. It does NOT apply to `./repo-core/` (the core repo isn't deployed as a standalone site).

   All subsequent steps reference `./repo-core/` as the primary workspace (where the fix is authored) and `./repo-client/` as the secondary (where the QA branch lands).

6. **Write `./plan.md`** with `superpowers:writing-plans`. Small, sequential, testable steps.

7. **Read the playbooks** that apply: `shared-development-guide.md` + `unit-testing-guide.md` always; civicrm/extension when touching that surface.

7a. **Pattern-reuse confirmation (after `./plan.md` is written, before implementation).**

   **For core-rooted bugs (classified at step 3.2):** confirm via `grep -rn`:
   1. The new behavior you're about to add in `./repo-core/` does NOT already exist there in a separate location. If it does, extend the existing handler instead of writing parallel code.
   2. The same behavior does NOT exist as a per-site re-implementation in `./repo-client/sites/all/themes/custom/*/` or `./repo-client/sites/all/modules/{custom,features}/*/` — if it does, the per-site implementation can be removed once the core fix ships.
   3. **Peer-handler audit.** If the new behavior binds to a generic Bootstrap selector that subthemes commonly rebind (`.navbar-toggler`, `.accordion-button`, `.dropdown-toggle`, `.modal`, `.collapse`, etc.), grep `./repo-client/sites/all/themes/custom/*/js/` and `./repo-client/sites/all/modules/{custom,features}/*/` for existing `$(selector).once(...)` or `$(selector).on(...)` bindings on the same selector. If any exist, the new behavior must either (a) supersede them — with corresponding subtheme strips applied in the QA-branch commit per step 11a — or (b) coordinate with them explicitly. Multiple handlers with different visibility logic on the same DOM element race; whichever runs last wins. The IESBUILD-247 follow-up case: the new `compu_bs5/js/header-popups.js` `navbarUserLoginMenuSync` ran alongside two IES `.navbar-toggler` handlers in `login-popup.js` and `logout-popup.js` (each using different `.once()` keys and different visibility logic), and Phase B verification passed by coincidence rather than by robust ownership.

   **For client-exclusive bugs (classified at step 3.2):** confirm via `grep -rn`:
   1. The new behavior you're about to add in `./repo-client/sites/all/themes/custom/<site>/` or equivalent does NOT have an equivalent handler in `./repo-client/profiles/compuclient/...` (vendored parent code).
   1b. The DOM IDs and selectors you plan to bind do NOT appear as the direct target of `.on()`, `.click()`, `.toggle()`, `.show()`, `.hide()`, or similar event/visibility calls — or as the subject of `Drupal.behaviors` attach functions — in `./repo-client/profiles/compuclient/...`. If they do, the fix is core-rooted regardless of whether you are adding or modifying behaviour: the core code owns the interaction lifecycle for those elements.
   2. **If either check fires**: re-classify as core-rooted. Restart from step 5 with the dual-clone setup — do NOT implement a per-site duplicate or overlay of a vendored core handler.

   This confirmation catches two related failure modes: **(a) the IESBUILD-247 case** — agent writes a per-site click-away handler (`menu-click-away.js`) for popup elements whose toggle is managed by `compu_bs5/includes/menu.inc`, passing check 1 by noting that `nested-dropdown.js` handles main-nav (not popups) — but failing 1b because `#cw-login-menu-popup-container` is a direct `.toggle()` target in `menu.inc`; **(b) the simpler duplication case** — agent writes a per-site handler that replicates an existing core handler verbatim.

   **Coordinator behavior gate (universal — applies before writing any new behavior, regardless of bug classification).**

   A *coordinator* is any new `Drupal.behaviors.*` implementation or document-level jQuery handler whose primary effect is to close, hide, collapse, or otherwise manage DOM elements whose **interactive lifecycle** (open/close/toggle) is already owned by another behavior or by inline JS in a PHP template function. Coordinators add a second controller on top of a broken first controller — they mask the root cause and break again when the first controller is later fixed.

   **Before writing any new behavior, answer:** who OWNS the open/close/toggle lifecycle of the affected DOM elements? The owner is the `Drupal.behaviors.*` implementation or PHP template function that creates, initializes, and binds the interaction for that element. Then:

   - **Owner is in `./repo-client/profiles/compuclient/...`** → the fix is **core-rooted** regardless of where your new behavior would live. Re-classify. Restart from step 5 with the dual-clone setup. Fix the owner in the core repo — do NOT write a coordinator in the client repo.
   - **Owner is in `./repo-client/sites/all/themes/custom/<site>/` or client modules** → edit the owner directly. A focused fix inside the owning behavior is always preferable to a new coordinator.

   **The IESBUILD-247 failure** is the canonical example: `menu-click-away.js` (a coordinator that calls `.hide()` on `#cw-login-menu-popup-container`) was written when `compu_bs5/includes/menu.inc` owns the popup toggle via `jQuery(document).ready() + .toggle()`. Correct fix: convert `menu.inc`'s popup code from raw `document.ready + .toggle()` to a `Drupal.behaviors.*` with its own click-away handler — an core PR against `compucorp/compu_bs5`. The `menu.inc` owner is in `profiles/compuclient/themes/contrib/compu_bs5/`, so this is core-rooted.

   **Follow-up commit selector discipline.** The grep checks above apply to the initial implementation. They also apply to any follow-up commit that adds a new CSS selector to an existing event binding (e.g. extending a behavior to cover an additional paragraph bundle or component variant). Before adding any new selector — even in a small follow-up commit — confirm it matches at least one DOM element by grepping the repo's templates (`*.tpl.php`, `*.html.twig`, theme hook suggestion files, and `*.module` theme function calls). A selector that matches nothing is dead code and will be flagged by the reviewer. Example failure: IESBUILD-232 — agent extended `.paragraphs-item-cw-carousel` to also cover `.paragraphs-item-cw-carousel-parallax` without verifying the parallax class exists; the parallax variant is rendered via a `theme_hook_suggestion` that swaps the `.tpl.php` but keeps the same bundle wrapper class — the extra selector was dead code, caught in reviewer round 2, and reverted.

8. **Implement with `superpowers:test-driven-development`.** Write a failing test that captures the bug. Make it pass with the smallest reasonable change.

   **Build-artifact policy (theme repos).** If you edit `*.scss`/`*.sass` in a theme directory that has a build script (`package.json` with a `"build"` script, or `gulpfile.js`), also regenerate the corresponding compiled CSS (`dist/css/*.css` or equivalent) and commit it in the same fix commit. `compucorp/ase` and `compucorp/ies` both serve the committed compiled CSS directly via theme `.info` declarations and do NOT rebuild in CI — an SCSS-source-only PR deploys with no styling change. If the local build produces output incompatible with the committed format (e.g., minified vs expanded), hand-append the compiled rules in the expected format and document this in PR `## Comments`. See `code-reviewer.md` "Build-artifact policy" section for the reviewer-side invariant.

9. **Verify with `superpowers:verification-before-completion`.** Run the tests. If the test suite requires a full Docker setup (CiviCRM `./scripts/run.sh setup`), do NOT run it locally — record `Tests not run locally — running on CI` and rely on CI green as the gate. For unit/script tests that run fast, run them and paste real output.

   **9a. Lint and document to the repo's enforced standard (mandatory before committing).**

   After implementing the fix, run the repo's linter on the changed files only. Do NOT run full-repo lint — legacy Drupal repos accumulate pre-existing violations that are not your responsibility.

   **Detect and run:**

   | Config file present | Linter | Command |
   |---|---|---|
   | `.eslintrc*` or `eslint.config.*` | ESLint | `./node_modules/.bin/eslint <changed-js-files>` |
   | `tsconfig.json` | TypeScript | `./node_modules/.bin/tsc --noEmit` |
   | `.phpcs.xml` or `phpcs.xml.dist` | PHPCS | `./vendor/bin/phpcs <changed-php-files>` |
   | `phpstan.neon*` | PHPStan | `./vendor/bin/phpstan analyse <changed-php-files>` |
   | `phpmd.xml` or `.phpmd*` | PHPMD | `./vendor/bin/phpmd <changed-php-files> text <ruleset>` |

   **Installation fallback for JS linters** (when `node_modules` is absent):

   ```bash
   npm ci --ignore-scripts       # preferred — respects lockfile
   npm install --ignore-scripts  # fallback if ci fails
   npm install --ignore-scripts --force  # fallback for peer-dep mismatches only
   ```

   If install still fails due to **Node version skew** (host Node too new/old for the package's engine range), do NOT attempt to work around it. Document in PR `## Comments`: "Linter skipped — Node version mismatch (host: `vX`, required: `<range>`). CI will catch lint errors." and proceed.

   **Fix all errors before committing.** Warnings are acceptable if they are pre-existing in the file (verify with `git stash && ./node_modules/.bin/eslint <file> && git stash pop` to confirm the warning existed before your change).

   **JSDoc and PHP docblocks:** Match the documentation style enforced by the repo's linter config:
   - If `eslint-plugin-jsdoc` is in devDependencies or `.eslintrc` extends, write complete `@param <type> <name> - <description>` and `@returns <type> <description>` for every function you add or modify.
   - If phpcs uses `Squiz.Commenting.FunctionComment` (common in Compucorp PHP), write complete `@param` and `@return` docblock lines for every method you add or modify.
   - Incomplete docblocks (type annotation present but description missing) are treated as errors by these rules and will fail CI.

   **Opportunistic linter-config fixes (see also step 11):** If running the linter reveals a config gap that causes errors on files OTHER than yours (e.g. a missing global in `.eslintrc.json`), fix the config in the same PR. Scope creep is allowed only when the gap directly blocks the linter from passing on your changed files. Call it out in PR `## Comments`: "Also fixed pre-existing `.eslintrc.json` gap (`bootstrap` global missing) which caused false positives on `popper-extras.js`."

10. **Visual verification (any ticket with an observable symptom).** Apply the two-condition gate from `prompts/visual-repro.md` § 1: (a) a specific staging URL is resolvable from the ticket (description, comments, or via step 3b Mongo lookup), AND (b) the URL passes `assert_staging_host`. The gate is no longer restricted to UI-changing diffs — backend tickets (PHP/hook/API/queue/email fixes) reproduce through the UI surface where their symptom appears (Civi admin view, `/admin/reports/dblog`, Mautic preview, SearchKit, etc.). If either condition fails, document the gate decision in PR `## Comments` (one line: "Visual repro skipped: <reason>") and proceed to step 11 with `## Manual verification required` in the PR body.

    When both conditions hold:

    10a. Read `prompts/visual-repro.md`.
    10b. Pick the simplest pattern (1/2/3) that fits the bug; copy the skeleton to `<workspace>/repro.py` (workspace root — NOT inside `./repo-client/` or `./repo-core/`).
    10c. Fill `reproduce(page)` and `assert_bug_reproduced(page)`. First line of `main()` must be `pathlib.Path("before.png").unlink(missing_ok=True)`. If the substantive diff is CSS-only per `prompts/visual-repro.md` §8's gate, ALSO add the after-state pass — see §8 for the code fixture, the inject-after-reproduce ordering, and the `assert_bug_fixed` contract.
    10d. Run: `cd <workspace> && python3 repro.py`. Outputs `<workspace>/before.png` on success (and `<workspace>/after.png` when §8 applies). (The `cd` is required because `page.screenshot(path="...")` is cwd-relative.)
    10e. **Gitignore policy (v1.12+, mandatory):** Before committing any artifacts, ensure `.agent-artifacts/` is present in the **client repo's** `.gitignore`. Check and add if missing:

    ```bash
    cd <workspace>/repo-client
    if ! grep -qF '.agent-artifacts/' .gitignore 2>/dev/null; then
      echo '.agent-artifacts/' >> .gitignore
      git add .gitignore
      git commit -m "{{ issue.identifier }}: gitignore agent scaffolding"
    fi
    ```

    With `.agent-artifacts/` gitignored, screenshots are **workspace-only** — they cannot be committed. This is correct by design. The `git add .agent-artifacts/` step from earlier policy versions (v1.5–v1.11) is **removed**. Screenshots captured to `<workspace>/before.png` and `<workspace>/after.png` stay in the workspace for operator audit and persist in the JSONL transcript; they do NOT enter the client repo's git history.

    If exit 0 AND `before.png` exists: screenshots are available at `<workspace>/before.png`. They will be referenced in the PR body using the dev-site URL (if 12b-bis ran) or the manual-verification block (if 12b-bis was skipped).

    PR `## Before` (when 12b-bis ran or dev-site URL is available):
    ```markdown
    Reproduction captured against `<staging URL>` via a Playwright assertion that fired before the screenshot was taken. Visual evidence: workspace `before.png` (see dev-site Phase A below).
    ```

    PR `## Before` (when 12b-bis was skipped):
    ```markdown
    ## Manual verification required

    Steps to reproduce: <URL>, <preconditions>, <what to look for>
    ```

    If `after.png` was captured (§8 CSS-only or 12b-bis Phase B), PR `## After` references the dev-site URL or notes the workspace capture.

    **Reproduction gate.** If exit non-zero OR `before.png` missing AND the two-condition gate at the top of step 10 was met (staging URL resolvable + host passes `assert_staging_host`): **STOP.** Do NOT proceed to step 11 or 12. Before stopping, try the small-element fallback: if the bug description references an icon, badge, narrow button, or any element below ~40px, re-run with `device_scale_factor=3` as described in `prompts/visual-repro.md` §9c. If §9c reproduces the bug, continue normally. If §9c also fails to fire `assert_bug_reproduced`: before declaring the bug unreproducible, ensure step 4a's data-state verification was completed — the entity may be correctly configured and the bug is in rendering logic, not in missing data. If 4a was completed and confirmed entity data is correct, post a Jira comment via the Atlassian MCP explaining (a) the URL tested, (b) the reproduction steps attempted, (c) that `assert_bug_reproduced` did not fire even at 3× DPI. Proceed to step 15 with prefix `blocked-verify`. _If `before.png` was captured before the gate failed, embed it inline using the screenshot embedding workflow above._

    If the gate at the top of step 10 was NOT met (no staging URL resolvable, or host did not pass `assert_staging_host`): document in PR `## Comments` ("Visual repro skipped: <reason>") and proceed with `## Manual verification required` in the PR body.

    10f. **Artifact lifecycle note (v1.12+):** Screenshots stay in the workspace and the JSONL transcript — they do NOT enter the client repo's git history. This avoids accumulating ~1–2 MB of CI tooling artifacts per ticket in client repo history (per Compucorp's engineering feedback on PR #229). If the team later wants permanent storage with public URLs, the correct solution is an S3/Cloudflare R2 bucket accessible to the bot PAT — that is the v2 path.

11. **Commit and push.**

   **Single commit per PR (default).** Every Symphony PR should land as a single commit. If the implementation produced multiple commits (e.g. a TDD test commit + an implementation commit, or a follow-up linter-fix commit on the same branch), squash them locally before push via `git reset --soft <base>` + recommit, so the branch presented for review has one commit with the full rationale in its body. This is what Compucorp reviewers (Ayush, Hitesh) consistently ask for; baking it in saves the round-trip.

   **Linter config fixes in the same commit (v1.12+):** If step 9a revealed a config gap (e.g. missing global in `.eslintrc.json`, missing rule in `phpcs.xml`) that caused errors on files outside your diff, include the config fix in the same commit as your code fix. Scope creep of this kind is allowed only when:
   - The gap directly prevents the linter from passing on your changed files, AND
   - The fix is mechanical (add a global, add an ignore rule) — not a policy change.
   Document it in PR `## Comments`.

   **Single-target (client-exclusive):** Branch `agent/{{ issue.identifier }}-fix` in `./repo-client/` (created from `BASE_COMMIT` per invariant 3 and step 3b). Commit message starts with `{{ issue.identifier }}:`. Push to the client repo remote.

   **Dual-target (core-rooted):** Two branches, two pushes, in this order:

   **11a. Propagate the fix to the client's vendored copy** (`./repo-client/`):

   1. Derive the target directory in the client repo. The mapping from core repo path to client vendored path is already encoded in step 3.2's table (e.g., core path `profiles/compuclient/themes/contrib/compu_bs5/` → `compucorp/compu_bs5`, so the mount point in the client is `profiles/compuclient/themes/contrib/compu_bs5/`). Use that mapping directly rather than `find`:
      ```bash
      # From step 3.2's mapping, e.g. for compu_bs5 → profiles/compuclient/themes/contrib/compu_bs5
      # For core-website module → profiles/compuclient/modules/contrib/core_website
      # APPLY_DIR is the prefix to prepend when applying the patch to ./repo-client/
      # e.g. APPLY_DIR="profiles/compuclient/modules/contrib/core_website"
      ```
      If the path is not in step 3.2's table (i.e., you reached this step via `compuco_projects.yml` lookup for an unlisted module), use `find` as a fallback:
      ```bash
      KEY_FILE=$(git -C ./repo-core diff-tree --no-commit-id --name-only -r HEAD | head -1)
      MATCHES=$(find ./repo-client/profiles/compuclient -path "*/${KEY_FILE}" -maxdepth 8 2>/dev/null)
      ```
      - One match → derive `APPLY_DIR` as the path under `./repo-client/` up to (but not including) the matched file's relative portion that exists in `./repo-core/`.
      - Zero matches → STOP; post Jira comment describing the mismatch.
      - Multiple matches → STOP; post Jira comment asking which path is correct.

   2. Ensure `./repo-client/` is at the correct base before branching. `BASE_COMMIT_CLIENT` is the client's deployed tag resolved in step 3b for `./repo-client/`. The core repo's base is called `BASE_COMMIT_CORE` (default branch tip — see step 5).
      ```bash
      # Ensure we branch from the client's deployed tag, not a random HEAD
      git -C ./repo-client checkout "$BASE_COMMIT_CLIENT"
      git -C ./repo-client checkout -b qa-{{ issue.identifier }}
      ```

   3. Run patch propagation. `APPLY_DIR` is relative to the **client repo root** (e.g. `profiles/compuclient/modules/contrib/core_website`). All commands run from the workspace root — use `-C ./repo-client` consistently so `git apply` writes into the correct directory tree:
      ```bash
      git -C ./repo-core diff HEAD~1..HEAD > /tmp/core.patch
      git -C ./repo-client apply --directory="$APPLY_DIR" --check /tmp/core.patch
      ```
      - **`--check` passes** (expected common case): apply the patch, commit:
        ```bash
        git -C ./repo-client apply --directory="$APPLY_DIR" /tmp/core.patch
        # Stage only the files touched by the patch (non-interactive)
        git -C ./repo-client add "$APPLY_DIR"
        git -C ./repo-client commit -m \
          "{{ issue.identifier }}: propagate core fix for QA testing"
        ```
        Set `PROPAGATION_STATUS=byte-identical`.

      - **`--check` fails**: attempt 3-way merge:
        ```bash
        git -C ./repo-client apply --directory="$APPLY_DIR" --3way /tmp/core.patch
        ```
        If 3way succeeds: `git -C ./repo-client add "$APPLY_DIR"`, commit, set `PROPAGATION_STATUS=context-resolved`. Note in Jira comment later.
        If 3way also fails: skip QA branch; set `PROPAGATION_STATUS=skipped`; note `AGENT_DONE` will be `success-core-only` if everything else passes.

   3a. **Cross-repo cleanup completeness.** If the core fix supersedes inline JS or `Drupal.behaviors` code that the client's subthemes also re-implement, grep `./repo-client/sites/all/themes/custom/*/js/` and `./repo-client/sites/all/modules/{custom,features}/*/` for every handler touching the **same DOM surface** the new core behavior now owns (same selector OR same popup/dropdown container ID OR same toggle-source class). Strip them all in the QA-branch commit — not just the most obvious duplicate. The IESBUILD-247 case: the agent stripped one `.toggle()` line in IES `logout-popup.js` but left two unrelated `.navbar-toggler` `.once()` bindings (in `login-popup.js` and `logout-popup.js`) attached to the same `.navbar-user-login-menu` element that the new core `navbarUserLoginMenuSync` behavior now owns. The orphaned handlers raced with the new behavior; the human reviewer caught it. Use `grep -rn` with the selectors / element IDs the core diff touches, not just an "obvious duplicate" line-level search.

   4. When `PROPAGATION_STATUS != skipped`: push the QA branch:
      ```bash
      git -C ./repo-client push <client-remote> qa-{{ issue.identifier }}
      ```
      **This push must happen before Phase B (the Jenkins dev-site deploy needs the branch on the remote) and before the reviewer runs (section 7 checks for its presence).**

   **11b. Push the core branch** (`./repo-core/`):
   Branch `agent/{{ issue.identifier }}-fix` (created from `BASE_COMMIT_CORE` per invariant 3 and step 5). Commit message starts with `{{ issue.identifier }}:`. Push to the core repo remote. The reviewer needs this branch for the diff.

12. **Independent code review + open the PR (single coupled step).** This pair is intentionally NOT split — see invariant 9.

   12a. **Dispatch code reviewer** via `Task` tool (`subagent_type: Plan`, `model: opus`). The reviewer reads `prompts/code-reviewer.md` and emits structured JSON per `prompts/code-reviewer-schema.json`.

   Pass it: ticket identifier+title+description+filtered comments, contents of `<workspace>/plan.md`, workspace path. If this is round N>1, also pass `prior_findings` (the `findings` array from `<workspace>/review-result-r<N-1>.json`). Save its output to `<workspace>/review-result-r<N>.json`.

   **Diff to pass:**
   - **Single-target:** `git diff <default-branch>..HEAD` from inside `<workspace>/repo-client/`
   - **Dual-target:** `git diff <default-branch>..HEAD` from inside `<workspace>/repo-core/` (the core PR's diff). The client QA branch diff is byte-identical (or close after 3-way) — do NOT send it separately; reviewer section 7 (dual-target completeness) verifies only that the QA branch push happened.

   **Additional v1.12 inputs to pass:**
   - `workspace_layout`: `"single"` or `"dual"`
   - `target_repo_type`: `"core"` (dual-target runs) or `"client"` (single-target runs)
   - `propagation_status`: `"byte-identical"`, `"context-resolved"`, or `"skipped"` (dual-target only; omit for single-target)

   12b. **Interpret the verdict** (loop per invariant 9):
   - `approve` → continue to 12c
   - `reject` with BLOCKERs/QUESTIONs and N < 3 → fix the BLOCKERs (revise plan + code), re-dispatch (back to 12a)
   - `reject` and N == 3 → STOP. Post Jira comment quoting `review-result-r3.json.findings` (BLOCKERs only) and the rounds attempted. Leave `agent:todo` label ON. Proceed to step 15 with prefix `blocked-review`. Do not open PR.

   12b-bis. **Two-phase dev-site verification** (mandatory if the target repo is in `SITE_DEPLOYABLE_REPOS` per invariant #5; skip with a one-line `## Comments` note otherwise — e.g. extension, theme, or infra repo). Runs ONLY after 12b returned `verdict: approve`; the reviewer evaluates code, the dev site evaluates the deployed result.

   **Dual-target note:** For core-rooted runs, both phases use `./repo-client/` as the deploy target — because the bug exhibits on the client site, not on the core extension in isolation. The `qa-<TICKET>` branch in `./repo-client/` must be pushed (step 11a) before Phase B triggers. If `PROPAGATION_STATUS == skipped`, Phase B is also skipped (no QA branch to deploy); `AGENT_DONE` becomes `success-core-only` after the core PR opens.

   **Skip guard — doc-only diffs:** Run `git diff --stat <default-branch>..HEAD` and if every changed path matches `*.md`, `*.txt`, or is comment-only, skip 12b-bis with `## Comments` note: "Dev-site step skipped: doc-only diff." Saves Jenkins on trivial PRs.

   Symphony's stall detector fires after ~5 min of no Claude API activity. Jenkins builds can run for up to 30 min. **Never run a poll loop in a single blocking Bash call.** The pattern below uses 90-second timeout chunks — each iteration is a fresh Bash call that resets the stall timer. Cap all poll loops at 20 re-runs (~30 min); treat cap as a failure per the failure table below.

   ---

   ### Phase A — Create dev site at broken tag (before.png)

   **Goal:** deploy the code that was live when the bug was filed, loaded with the best available database, so `assert_bug_reproduced` fires on the dev site and we can capture `before.png` on identical infrastructure to Phase B.

   **A1. Resolve the broken tag.**
   Use `BASE_COMMIT` already in scope from step 3b (resolved from Mongo `sites.images.php` when the staging site was first inspected). Do **not** re-query Mongo — staging may have been re-deployed since.

   **A2. Pick the database source** based on where the bug was reported:
   - **Bug reported on a staging site** (hostname ends `.cc-staging.site`): pass the bare staging hostname as `anondb_url` (e.g. `ies2.cc-staging.site`). The Groovy pipeline generates S3 presigned URLs for that hostname's backup bucket automatically.
   - **Bug reported on production or any other site**: call `resolve_anondb_url("<ticket-hostname-from-3b>")` from `repro_helpers` — returns the client-matched anondbs URL (e.g. `https://anondbs.cc-infra.tools/dir.php?name=…`). If it returns `None` → **STOP** per the failure-modes table (anondb None row); post Jira comment, apply `agent:blocked`, proceed to step 15 with prefix `blocked-verify`. Do NOT continue to 12c.

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
   Exit code `42` → sleep 60 s, re-run **`poll_phase_a.py`** (NOT `trigger_phase_a.py` — the job is already queued/running; re-running the trigger creates a duplicate dev site). `0` → proceed to A5. Any other non-zero or cap exceeded → **Phase A failure** (see failure table; **STOP** — post Jira comment, apply `agent:blocked`, proceed to step 15 with prefix `blocked-verify`).

   **A5. Capture before.png:**

   Run `visual-repro.md §9a`: reproduce the bug → `assert_bug_reproduced` → capture `before.png`. Save to `<workspace>/before.png` and copy to `repo/.agent-artifacts/{{ issue.identifier }}/before.png`. Commit on the agent branch (second commit after the fix commit — intentional append post-approval).

   **Reproduction gate.** If `assert_bug_reproduced` does **not** fire: **STOP.** Do NOT fall back to staging `before.png`. Do NOT continue to Phase B. Post a Jira comment via the Atlassian MCP explaining (a) the dev site URL tested, (b) the `reproduce()` steps attempted, (c) that `assert_bug_reproduced` did not fire on the dev site after the broken tag was deployed. Proceed to step 15 with prefix `blocked-verify`. _If `before.png` was captured, embed it inline using the screenshot embedding workflow above._ A fix that cannot be confirmed as reproduced on real infrastructure must not be shipped.

   **A6. Clean up the before tag:**
   ```bash
   git push origin --delete agent-{{ issue.identifier }}-before
   ```

   ---

   ### Phase B — Release fix branch to same dev site (after.png)

   **Goal:** deploy the agent's fix branch to the same dev site (same data, no DB reimport) and assert the bug is gone.

   Before starting B1, update the step status file:
   ```bash
   echo '{"step": 13, "total": 15, "label": "Dev-site Phase B (deploy fix + after.png)"}' > .symphony-status.tmp && mv .symphony-status.tmp .symphony-status
   ```

   **B1. Push the fix tag and trigger Phase B (fast, one-shot):**

   Both single-target and dual-target create the Jenkins tag in `./repo-client/`. The tag name must be Docker-safe (no `/`) and identifiable — use `agent-{{ issue.identifier }}-fix` in both cases. The difference is which commit it points to:

   **Single-target:**
   ```bash
   cd <workspace>/repo-client
   # HEAD is already on agent/{{ issue.identifier }}-fix branch
   git tag agent-{{ issue.identifier }}-fix HEAD
   git push origin agent-{{ issue.identifier }}-fix
   ```

   **Dual-target:**
   ```bash
   cd <workspace>/repo-client
   # Checkout the qa branch (already pushed in step 11a) to ensure HEAD is correct
   git checkout qa-{{ issue.identifier }}
   git tag agent-{{ issue.identifier }}-fix HEAD
   git push origin agent-{{ issue.identifier }}-fix
   ```
   Note: the tag `agent-{{ issue.identifier }}-fix` on the client remote is a Jenkins deployment tag, intentionally separate from the `qa-{{ issue.identifier }}` branch name. Both exist on the client remote; the tag is deleted after Phase B (B4) per the orphan-tag cleanup rule.
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
   import pathlib, sys, requests, os
   queue_url    = pathlib.Path("<workspace>/.release-queue").read_text().strip()
   devsite_host = pathlib.Path("<workspace>/.devsite-host").read_text().strip()
   # Restart-safe: if the queue item was purged by Jenkins (~5 min after build
   # starts), the queue URL returns 404. Read the cached build URL instead.
   release_build_url_file = pathlib.Path("<workspace>/.release-build-url")
   release_build_url = (
       release_build_url_file.read_text().strip()
       if release_build_url_file.exists() else None
   )
   # If we don't have a cached build URL yet, resolve and cache it now.
   if release_build_url is None:
       try:
           r = requests.get(f"{queue_url}api/json",
                            auth=(os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"]),
                            timeout=15)
           if r.status_code != 404 and r.json().get("executable", {}).get("url"):
               release_build_url = r.json()["executable"]["url"]
               release_build_url_file.write_text(release_build_url)
       except Exception:
           pass  # poll_until_released will handle 404 with guidance
   result = poll_until_released(queue_url, site_url=devsite_host,
                                build_url=release_build_url,
                                timeout_s=90, raise_on_timeout=False)
   if result is None:
       print("Phase B still running — re-run to continue polling")
       sys.exit(42)
   wait_until_site_up(devsite_host, timeout_s=300)
   pathlib.Path("<workspace>/.release-done").write_text("ok")
   print(f"PHASE_B_DONE host={devsite_host}")
   ```
   Exit code `42` → sleep 60 s, re-run **`poll_phase_b.py`** (NOT `trigger_phase_b.py` — the job is already queued/running; re-running the trigger creates a duplicate release). `0` → proceed to B3. Any other non-zero or cap exceeded → **Phase B failure** (see failure table; **STOP** — post Jira comment, apply `agent:blocked`, proceed to step 15 with prefix `blocked-verify`).

   **B3. Capture after.png (and optionally after.gif):**

   Before capturing, **re-evaluate the `visual-repro.md §10.1` gate**: is the bug's evidence a sequence of interactions — an animation completing, a counter updating, a dropdown closing, a state transition playing out? If yes, enable video recording for this Phase B context pass and convert to GIF per §10.3 (upload to S3 per §10.5). Specify what the GIF should show — e.g. for a carousel bug: slide animating while counter increments, indicator highlight moving, wrap-around from last to first slide. If the fix is purely CSS/static (colour, layout, spacing), skip video and take a static screenshot only.

   **When a GIF is produced**, reference the S3 URL in the PR `## After` section in addition to `after.png`: `![After](https://<bucket>.s3.<region>.amazonaws.com/<TICKET>/after.gif)`. This replaces the static `after.png` inline image — the GIF conveys the same information and more. Still capture `after.png` as a workspace artifact (for the audit trail); only the GIF goes into the PR body.

   Run `visual-repro.md §9b`: `assert_bug_fixed` → capture `after.png`. Save to `<workspace>/after.png`. Per the v1.12 gitignore policy (step 10e), screenshots are workspace-only — do NOT commit to the repo.

   If `assert_bug_fixed` **fails** (assertion didn't fire): the agent's fix didn't take. **You may attempt recovery at most once per ticket** (not per failure, not per Phase B attempt — exactly one recovery cycle for this entire run, regardless of how the agent got into Phase B). Diagnose the failure (live browser eval, jQuery `_data(document, 'events')`, computed-style checks, etc.), commit a follow-up fix to the same branch (`agent/{{ issue.identifier }}-fix`), re-tag, re-trigger Phase B from B1, and re-assert. If the **second** `assert_bug_fixed` also fails: **BLOCK** the PR. Post a Jira blocker comment quoting (a) the reviewer's approval, (b) both Jenkins build numbers + dev-site URL, (c) the assertion failure, (d) any diagnostic you gathered, and (e) likely cause: "DB or data state may not reproduce the bug on the dev site, or the fix has a residual defect that needs human review." Leave `agent:todo` ON. Proceed to step 15 with prefix `blocked-verify`. Do not run Phase B a third time — the cap is a hard limit per ticket.

   **B4. Clean up the fix tag:**
   ```bash
   git push origin --delete agent-{{ issue.identifier }}-fix
   ```

   ---

   ### Failure modes

   | Failure | Behaviour |
   |---|---|
   | Repo not in `SITE_DEPLOYABLE_REPOS` | **STOP.** Post Jira comment via the Atlassian MCP naming the target repo and noting Symphony has no dev-site path for it. Proceed to step 15 with prefix `blocked-verify`. Apply the `agent:blocked` label (see step 14). Operator decides whether to (a) add the repo to the allowlist if a dev-site path exists, (b) verify manually + override, or (c) cancel. |
   | Doc-only diff | Skip both phases. One-line `## Comments` note. Continue to 12c. |
   | anondb lookup returns `None` | **STOP.** Post Jira comment via the Atlassian MCP naming the hostname that returned None and the Mongo lookup query attempted. Proceed to step 15 with prefix `blocked-verify`. Apply the `agent:blocked` label (see step 14). |
   | Phase A FAILURE / timeout / cap | **STOP.** Post Jira comment via the Atlassian MCP naming the Jenkins build URL and likely cause (HTTP code, timeout, build error message). Proceed to step 15 with prefix `blocked-verify`. Apply the `agent:blocked` label (see step 14). Common cause: bot user lost permission on the Jenkins job (Jenkins returns 404 for unauthorized paths to hide existence; an HTTP 404 from `_DEVSITE_JOB_PATH` typically means a permission change, not a moved job). |
   | `assert_bug_reproduced` doesn't fire | **STOP.** For sub-40px element bugs (icons, badges, narrow borders), retry once at `device_scale_factor=3` per `visual-repro.md` §9c BEFORE stopping. If §9c also fails: post Jira comment (URL tested, steps attempted, assertion did not fire even at 3× DPI). Proceed to step 15 with prefix `blocked-verify`. Do NOT open PR. |
   | Phase B FAILURE / timeout / cap | **STOP.** Post Jira comment via the Atlassian MCP naming the Jenkins build URL and likely cause. Proceed to step 15 with prefix `blocked-verify`. Apply the `agent:blocked` label (see step 14). |
   | `assert_bug_fixed` fails on dev site | Attempt recovery (diagnose + commit fix + re-trigger Phase B) **at most once**, per the recovery paragraph above. If the second attempt also fails: **Block PR.** Proceed to step 15 with prefix `blocked-verify`. Jira blocker comment. |

   _For all failure-mode block comments: if a screenshot (`before.png`, `after.png`) was captured before the failure point, embed it inline using the screenshot embedding workflow above._

   **Orphan-tag note:** A6 and B4 push-delete the Jenkins tags after each phase. If the agent crashes or is interrupted between the tag push and the delete, `agent-<TICKET>-before` and/or `agent-<TICKET>-fix` tags will leak on the remote. They are harmless (lightweight tags; no CI triggers on them) but accumulate over time. If you notice orphan `agent-*` tags when inspecting a repo, delete them manually with `git push origin --delete <tag-name>`.

   ### PR-body additions (12c)

   - `## Before` — references dev-site `before.png` if captured; otherwise staging `before.png` from step 3b.
   - `## After` — `after.png` if captured, plus: `Live verification at https://<host> (auto-expires <date>).`
   - `## Comments` — one line per job: "Phase A: Jenkins build #N, tag=`<before_tag>`, anondb=`<url>`." and "Phase B: Jenkins build #N, tag=`<fix_tag>`."

   When 12b-bis runs successfully, `visual-repro.md` §8's inject-based `after.png` path is **superseded** — do not run it. The §8 path only fires when 12b-bis was skipped AND the diff is CSS-only.

   12c. **`gh pr create`** — Only after 12a was dispatched AND 12b returned `verdict: approve` on the latest round AND (12b-bis ran to completion OR 12b-bis was skipped per its own gates — but NEVER if 12b-bis blocked). Never run `gh pr create` directly without those rounds having been the final actions; running it bypasses the invariant #9 gate. The audit (`analyze-run.sh`) reports the reviewer-dispatch count and the `gh pr create` count separately — an operator inspecting the run will see immediately if the latter happened without the former and treat that as a workflow violation. Body follows `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` exactly (Overview / Before / After / Technical Details [with `### Core overrides` subsection if applicable] / Comments — see invariant 4). The PR body's `## Comments` section lists any WARNINGs/SUGGESTIONs from the final reviewer round that you chose to document rather than fix, with brief reasoning per item. Do NOT mention the reviewer subagent in the body — that's internal process; the PR's `## Comments` should read as concrete reviewer guidance, not as audit trail.

   **PR gate (first-class invariant):** the PR — whether against a client repo or a core repo — opens **ONLY** after Phase B's `after.png` confirms `assert_bug_fixed` fired in the deployed environment. The single exception is when step 12b-bis was legitimately skipped per the **Doc-only diff** row of the failure-modes table (no runtime to verify). In every other case where Phase A or Phase B couldn't complete — Jenkins infra failure, anondb missing, repo not in `SITE_DEPLOYABLE_REPOS`, assertion failure after the one allowed recovery attempt — **STOP.** Proceed to step 15 with prefix `blocked-verify`. Leave any pushed branches (`agent/<TICKET>-fix` on the core repo for dual-target; `qa-<TICKET>` on the client repo) for operator inspection. Apply the `agent:blocked` label (step 14). An unverified PR — especially a core PR that would propagate via the next Compuclient release — is worse than a blocked ticket: it looks like a clean success on the dashboard but ships untested code.

   _Footnote on other non-runtime diff categories:_ tests-only, lint-config-only, and CI-config-only diffs currently block at the `assert_bug_reproduced` gate (no runtime symptom to assert against). That's the right outcome for now — those tickets rarely auto-flow. If non-runtime auto-PRs become a friction point, extend the legitimate-skip list explicitly rather than weakening the PR gate.

   **Single-target:** PR targets the client repo's default branch (`master` for most client repos, or the RC branch if one is active).

   **Dual-target (core-rooted):** PR targets the **core repo**'s default branch. `gh pr create` runs from inside `<workspace>/repo-core/`. No PR is opened on the client repo — only the `qa-<TICKET>` branch push (already done in step 11a). **RC override:** if a Compuclient release is mid-flight and the core PR should target an active RC branch instead of `master`, the operator changes the PR base after `gh pr create` — Symphony v1.13 always targets default. (Same note in step 5; restated here because it's where the change happens.)

   When `PROPAGATION_STATUS == skipped` (3-way merge failed): still open the core PR. Note in PR `## Comments`: _"Client QA branch propagation failed — vendored copy has diverged. See Jira comment for operator instructions."_ `AGENT_DONE` will be `success-core-only`. For the PR body's `## After` section, use the manual-verification block (step 10e's `## Manual verification required` template) — there is no `after.png` and no dev-site Phase B in this path.

13. **Post the PR link + QA branch as a Jira comment** via the Atlassian MCP.

   **Deduplication guard (mandatory).** Before posting, fetch the ticket's existing comments (`getJiraIssue` with `fields: ["comment"]`) and scan for any comment whose body already contains the PR URL you are about to post. If found, skip this step entirely — a duplicate comment causes confusion for reviewers and signals a workflow re-run. Log: "Jira comment skipped — PR URL already present in comment `<id>`."

   **Format.** Always use ADF `inlineCard` nodes for all URLs in the comment — GitHub URLs (PR links, branch links) and dev-site URLs alike. Plain-text URLs do not render as smart link cards in Jira. Use `contentFormat: "adf"` and wrap each URL in:
   ```json
   { "type": "inlineCard", "attrs": { "url": "<URL>" } }
   ```

   **Dev site link.** If Phase B ran successfully, include the dev site URL (`DEVSITE_HOST` from the Phase B poll output) in the comment so the QA team can visit the live fix directly. Note the auto-expiry: dev sites are typically live for 24–48 hours after the run.

   **Single-target:** one concise comment, e.g.:

   > PR: `<inlineCard: https://github.com/compucorp/<repo>/pull/<N>>` — please review.
   >
   > _(if Phase B ran)_ Live fix: `<inlineCard: https://<devsite-host>>` (dev site, auto-expires ~24 h).

   **Dual-target:** single comment with both links:

   > Core PR (the actual fix): `<inlineCard: https://github.com/compucorp/<core>/pull/<N>>`
   >
   > Client QA branch (for QA team testing): `<inlineCard: https://github.com/compucorp/<client>/tree/qa-{{ issue.identifier }}>`
   >
   > _(if Phase B ran)_ Live fix on dev site: `<inlineCard: https://<devsite-host>>` (auto-expires ~24 h).
   >
   > Workflow: QA team checks out the client QA branch on a test deployment, validates the fix in client context, then approves the core PR for merge. Once the core PR merges and is included in the next Compuclient release, this ticket can close as fixed-in-core.

   When `PROPAGATION_STATUS == skipped`:

   > Core PR (the actual fix): `<inlineCard: https://github.com/compucorp/<core>/pull/<N>>`
   >
   > Client QA branch: propagation failed (`git apply --3way` conflict — vendored copy has diverged from core). Manual action: (a) cherry-pick the core commit onto a fresh `qa-{{ issue.identifier }}` branch resolving the conflict by hand, or (b) wait for the core PR to merge into the next Compuclient release and the fix will propagate automatically.

   _If `after.png` was captured during Phase B, embed it inline using the screenshot embedding workflow above. Upload `after.png` as an attachment first, then include `!<confirmed_filename>|width=800!` at the end of the comment body after the PR link text. Use the v2 REST API endpoint for this comment (not the Atlassian MCP), since the wiki markup embedding requires v2. Note: the deduplication guard still applies — check for the PR URL in existing comments before posting._

14. **Resolve the labels** on the ticket via the Atlassian MCP.

   **Decide "blocked vs completed" from this session's actual outcome** — did Phase B's `assert_bug_fixed` fire (after recovery if applicable)? Did `gh pr create` succeed? Do NOT try to read `AGENT_DONE` to decide — the file does not exist yet (it is written only at step 15).

   **On the success path:** remove the `agent:todo` label via the Atlassian MCP (`editJiraIssue` with `update.labels.remove`). Do NOT apply `agent:blocked`. The label removal signals Symphony you're done — otherwise Symphony will keep re-dispatching this ticket on every poll.

   **On the block path** (any `blocked*` prefix in step 15): **leave** `agent:todo` on AND **apply** the `agent:blocked` label via the Atlassian MCP (`editJiraIssue` with `update.labels.add`). The `agent:blocked` label is purely informational — it makes blocked tickets filterable from any Jira board. Symphony's own poll / dispatch / preflight logic does NOT read this label; removing it has no effect on Symphony's behavior. Retry is operator-driven via workspace rename + `agent:todo` re-apply. Single-click retry-from-Jira is tracked as BACKLOG item 21 (`agent:retry` label, deferred to v1.14).

   **Label auto-creation note:** Atlassian Cloud auto-creates labels on first use, so no Jira admin action is required to provision `agent:blocked` before this lands.

15. **Write `AGENT_DONE` and stop.** This is the **only** place in the standard Routine (steps 1–15) where `<workspace>/AGENT_DONE` is written. Earlier steps that say "proceed to step 15 with prefix X" route here with the correct prefix; they never write the file themselves. (The dry-run mode at the top of this document has its own terminal write — that is a separate mode, not the standard Routine.) Once `AGENT_DONE` is written, the run is over: do NOT continue tool calls, do NOT retry, do NOT update the file. If you wrote `AGENT_DONE` and you are still active in the session, the workflow has been violated.

   Choose the prefix based on the run's outcome:

   - **Single-target success:** `success <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Dual-target success (QA branch pushed successfully):** `success-dual <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Dual-target (propagation failed, core PR only):** `success-core-only <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Blocked at classification (step 3.2 Uncertain) or environmental blocker (see "Blockers" section below):** `blocked <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Reviewer rejected at N=3 (step 12b):** `blocked-review <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Reproduction or verification failed (step 10d, Phase A A5, Phase B `assert_bug_fixed` after recovery, or Core PR gate):** `blocked-verify <ISO-8601-timestamp> {{ issue.identifier }}`
   - **Entity data-state confirmed as root cause (step 4a: no code bug):** `blocked-data <ISO-8601-timestamp> {{ issue.identifier }}`

   Do not transition the Jira status yourself — leave that to the human reviewing the PR.

## Blockers

If you hit any of these, stop and post a single Jira comment describing the blocker and exit:

- Ticket doesn't map to a repo on the allowlist.
- You need credentials/access not present in the environment.
- The fix requires touching infrastructure (Jenkins, Docker Swarm, CloudFlare config) — out of scope for Phase 1.
- The bug cannot be reproduced and there is no test that can be written for it without speculative changes.

When blocked, the Jira comment should state: what's missing, why it blocks the work, and the concrete human action required to unblock. After posting the comment, proceed to step 15 with prefix `blocked` (do not write `AGENT_DONE` here — step 15 is the only write site).

## AGENT_DONE schema

`AGENT_DONE` is a single-line sentinel file with exactly three space-separated fields and exactly one of the allowed prefixes:

```
<prefix> <ISO-8601-timestamp> <issue.identifier>
```

| Prefix | Meaning | Written by |
|---|---|---|
| `success` | Single-target run: PR opened, Jira commented, label removed. | Step 15 |
| `success-dual` | Dual-target run: core PR opened AND client QA branch pushed AND dual-link Jira comment posted. | Step 15 |
| `success-core-only` | Dual-target run: core PR opened successfully, but client QA branch push failed (3-way merge conflict on patch propagation). Jira comment includes operator instructions for manual QA branch creation. | Step 15 |
| `dry-run` | DRY-RUN OVERRIDE ran through step 12a, reviewer approved, no external side effects. | DRY-RUN OVERRIDE block |
| `blocked-review` | Reviewer subagent rejected at N=3 (invariant #9 loop limit). | Step 12b |
| `blocked-verify` | Reviewer approved, dev-site deploy succeeded, but `assert_bug_fixed` did not fire — typically the fresh anondb lacks the data state that triggers the bug. Operator decides whether to seed data + retry, push the PR manually after sanity-checking the dev site, or widen anondb selection. | Step 12b-bis |
| `blocked-data` | Step 4a confirmed the entity is misconfigured (NULL column, unpublished, date not met) — the bug is a data issue, not a code issue. Jira comment quotes structural evidence. | Step 4a |
| `blocked` | Generic blocker (Blockers section: repo not on allowlist, missing credentials, infra-touching scope, irreproducible bug, uncertain classification at step 3.2). | Blockers section |

Any other prefix, missing fields, malformed timestamp, or mismatched `issue.identifier` is a workflow bug and must be flagged by `analyze-run.py`. Operators rely on these strings to triage runs at a glance; do not invent new prefixes without updating this schema first.

## Step reporting

At the start of **each numbered step**, before doing any work for that step, write the following file to the workspace root:

```bash
echo '{"step": N, "total": T, "label": "Step heading text"}' > .symphony-status.tmp && mv .symphony-status.tmp .symphony-status
```

Where `N` is the current step number (1-based), `T` is the total number of steps, and `"Step heading text"` is the exact heading of that step. The atomic rename (write to `.tmp` then `mv`) prevents partial reads.

This file is read by the Symphony dashboard to show real-time progress. It costs zero tokens.

## PR URL reporting

Immediately after `gh pr create` succeeds, write the PR URL to the workspace:

```bash
echo '<pr_url>' > .symphony-pr-url
```

Replace `<pr_url>` with the URL returned by `gh pr create` (e.g. `https://github.com/org/repo/pull/123`). This file is read by the Symphony dashboard to display a link in the Recent sessions table.
