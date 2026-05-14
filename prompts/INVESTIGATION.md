# Bug-fix investigation flow

Adapted from `openclaw-configurations/incident_check/WORKFLOW.md`. The incident playbook is built around correlating outage evidence; **this one is built around reproducing a bug**. The discipline is the same — evidence before hypothesis, time-bounded checks, cross-correlate — but the goal is "I can make the bug happen on demand and see exactly what changes", not "I can explain why the site went down at 14:32".

## Prerequisites

This file is the **methodology** for investigating a bug-fix once you've committed to working on the ticket. **Before reading this**, you must have already cleared `WORKFLOW.md` step 1a (triage-conflict check) and step 3a (mirror check). If either of those flagged a blocker, you've already stopped and commented on Jira — you should not be reading this file in that case.

## Core principles

- **Evidence before hypothesis.** State what you saw before what you think it means.
- **Distinguish:** evidence (raw signal) vs interpretation (your reading) vs hypothesis (what you'd test) vs recommendation (what to change).
- **Time-bound everything.** Start with the narrowest plausible window and expand only when evidence justifies it.
- **One check, one question.** Each tool call should answer something specific. If you can't say what question a check answers, skip it.

## Required input

Before broad investigation, make sure you have:

- **Site / service / component** — what is broken
- **Symptom** — what specifically does the user see (error message, wrong result, missing UI, slow response)
- **Reproduction signal** — a deterministic way to trigger it (URL, command, sequence of clicks, payload)
- **Time window** — when did this start? Has it always been broken or is it a regression?
- **Scope** — is it one user, one tenant, one config, or everyone?

If any of these is unclear from the ticket, read all the ticket comments, then look in linked PRs / logs / referenced runbooks. If you still don't have a reproduction signal at the end, that's a blocker — comment on Jira and exit.

## Workflow

### 1) Identify the surface

For the affected site/service, derive:

- **Target repo** — from the allowlist. If ticket doesn't clearly map → blocker.
- **Branch to fork from** — usually `main` unless ticket says otherwise.
- **Stack** — CiviCRM extension? CiviPlus core? Drupal? Plain PHP? (Determines which playbook to read.)

### 2) Reproduce the bug

The goal of this step is a single command, URL, or payload that reliably reproduces the bug. Without that, the fix cannot be verified and the agent should stop.

- If the ticket has a reproduction recipe, use it.
- If the ticket has only a screenshot or vague description, try the obvious paths (the URL mentioned, the form submitted, the action performed) and see if you can hit the same symptom.
- If you can't reproduce in a reasonable amount of effort (≤ 30% of `max_turns`), stop and post a Jira comment describing what you tried.

### 3) Anchor the bug in code

Once reproducible, find the code path. Tools by symptom:

- **PHP error or exception** → grep the message in the repo, follow the stack
- **Unexpected behavior, no error** → grep the user-facing string / route / API call in the repo
- **Wrong data in DB or display** → trace the write path (form submit handler / API endpoint / cron job)
- **Recent regression** → `git log -p <suspect-area>` since the last known-good version

Cross-check with logs (Loki for production-side, local error logs for dev) **only if** the in-code trace is ambiguous. Don't burn turns on Loki for a bug you can already see in the source.

### 4) Form one specific, testable hypothesis

State it explicitly. Example: "Function `Civi\X::doThing()` does not check `Y` before calling `Z`, so when input `Y=null` the call fails with `TypeError` at line N."

A good hypothesis:

- names the file + function + line
- predicts what changes when you fix it
- can be falsified by a test

If you have more than one plausible hypothesis, list them and pick the most likely. Note the alternatives in the workpad.

### 5) Write the failing test

Before fixing anything. The test is the falsifier:

- If you can't write a test for it, the hypothesis is probably too vague — go back to step 4.
- Match the repo's existing test style (PHPUnit, Codeception, Drush test, etc — see `dev-ai-playbooks/.ai/unit-testing-guide.md`).
- The test must reproduce the bug **as the user experiences it**, not just exercise the line you suspect.

### 6) Implement the smallest fix

Code change should be the minimum required to make the test pass. Don't refactor surrounding code, don't fix other latent issues, don't add error handling for scenarios the test doesn't cover. Those are separate tickets.

### 7) Verify

Run the test. Then run the wider test suite if it's fast (unit/integration that don't require Docker setup). Capture the real output to mention in the PR's `## Comments` section.

If the suite requires `./scripts/run.sh setup` (Docker, slow), don't run it locally — say so in `## Comments` ("Tests not run locally — relying on CI") and rely on CI green.

### 8) Cross-correlate one more time

Before opening the PR, ask:

- Does the fix make sense with the evidence from step 1?
- Could there be a second site or context where the same bug surfaces and the fix would not cover it?
- Did the fix touch any code path I don't have a test for?

If any "yes" is concerning, mention it in the PR `## Comments` section.

## Expected output for the PR

**The PR body MUST follow the Compucorp `PULL_REQUEST_TEMPLATE.md` exactly** (see `dev-ai-playbooks/.github/PULL_REQUEST_TEMPLATE.md` and `shared-development-guide.md` §3):

- `## Overview` — non-technical, 1-2 sentences.
- `## Before` — current state. Screenshots/gifs for UI changes (or explicit "Screenshots to be added before merge" note if you couldn't capture them).
- `## After` — what changed. Same screenshot rule.
- `## Technical Details` — code-level details, file:line references, snippets. Subsection `### Core overrides` only if you patched a CiviCRM core file.
- `## Comments` — anything else: caveats, manual verification steps, test/lint status, related triage context.

Do NOT use the schema `Summary / Evidence / Root cause / Fix / Verification` — that's not the Compucorp template.

Do NOT add an "About this PR" / "🤖" / "Generated by" section — `shared-development-guide.md §5` forbids AI attribution.

Do NOT leak internal scaffolding into the PR body: no mention of Symphony, the workflow, workspace paths, or anything from this orchestration's plumbing. The PR should be indistinguishable from a careful human's work.

Keep it specific. A reviewer should be able to read the PR body and confidently say "yes, that's the bug, and yes, that's the right fix" without leaving the page.
