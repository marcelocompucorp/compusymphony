# Symphony Backlog

Engineering feedback items deferred from the v1.13 batch, pending a future planning round. Item numbers continue from the v1.13 feedback list (items 1–16); new items start at 17.

---

## Item 17 — Ticket-symptom grounding gate

**Status:** Open. Surfaced by IESBUILD-229 (PR #231, 2026-05-20).

**Problem.** v1.13's reproduction gate (item 2, commit `09f3c1b`) verifies that the agent can reproduce *some* anomaly on staging / dev-site before opening a PR. It does NOT verify that the reproduced anomaly is the one the ticket describes.

**Concrete failure case.** IESBUILD-229 ticket title: *"FAQ Listing - Cursor appears behind the 'expand' plus sign"* — describes a `cursor: pointer` (mouse-cursor styling) issue. The agent instead reproduced a different real bug visible at 3× DPI (a `\` stripe through the `+` icon caused by `compu_bs5`'s mask + IES's chevron CSS conflict), declared *that* the ticket's bug, and shipped a fix for it. PR #231 lands on `compucorp/ies` — procedurally correct (right repo per v1.13 classification, all gates fired, AGENT_DONE = success) — but solves a different problem than the one filed.

**Why v1.13 didn't catch this.**
- Reproduction gate (item 2): asks *"can you screenshot something anomalous?"* — yes, the agent screenshotted the `\` stripe.
- Small-element screenshots (item 4): made the wrong artifact more visible, ironically reinforcing the misdiagnosis.
- Code reviewer (step 12a/12b, `prompts/code-reviewer.md`): reviews whether the code is good, not whether the agent solved the right problem.
- No step asks: *"Restate the ticket's symptom. Does what you reproduced match it?"*

**Proposed gate (sketch — for a v1.14 plan to refine).**

Add a *symptom-grounding* check at the end of step 6 (write `./plan.md`) or beginning of step 10 (visual verification), before implementation:

1. In the plan's Context section, agent must explicitly restate the symptom from the ticket title + description + first-mention comment, in its own words.
2. If the ticket has attached images, agent must fetch them via the Atlassian MCP and compare visually to its `before.png`:
   - Same element / region of the page? (e.g. both show the FAQ accordion button)
   - Same kind of artifact? (e.g. both show a cursor styling issue, not "one shows cursor wrong, other shows icon mask wrong")
3. If symptom restatement diverges meaningfully from the ticket's wording, OR if the visual comparison shows different artifacts: **STOP**. Post a Jira comment asking the reporter to confirm which symptom is the one to fix. Set `AGENT_DONE = blocked` (not `blocked-verify` — this is a comprehension blocker, not a reproduction blocker).
4. If the ticket has NO image and the description is ambiguous (e.g. "the FAQ is broken"), bias toward stopping rather than guessing. The cost of asking for clarification once is far below the cost of shipping a wrong fix.

**Implementation locations (to be confirmed during planning):**
- `WORKFLOW.md` step 6 (`./plan.md`) — add a "Context: symptom restatement" mandatory subsection.
- `WORKFLOW.md` step 10 — add ticket-image fetch + visual comparison as a step before `repro.py`.
- `prompts/code-reviewer.md` — optional companion check: reviewer reads the plan's symptom restatement against the ticket and flags mismatches.

**Open questions for v1.14 plan stage:**
- How to fetch ticket attachments via Atlassian MCP? (`getJiraIssue` may not return attachment URLs cleanly — needs verification.)
- For tickets with screenshots, what's an acceptable visual-comparison tool? PIL pixel-diff is too brittle; LLM-based "do these images show the same kind of issue" is fuzzier but more useful.
- Should the gate run unconditionally, or only for "visual" tickets (any ticket whose description references rendering/appearance)?

**Action for IESBUILD-229 PR #231:** Close with explanation, do NOT re-apply `agent:todo` until item 17 is implemented — otherwise the next dispatch will repeat the same misdiagnosis.

---

## Item 16 — IESBUILD-232 / PR #230 (paused)

**Status:** Paused pending Hitesh's view on core carousel architecture.

**Background.** PR #230 in `compucorp/ies` (IESBUILD-232: carousel navigation indicator on slide change) added carousel slide-event JS to the IES per-site theme. Per v1.13 item 3 (coordinator anti-pattern) and item 7 (classification), this *might* belong in `compu_bs5` rather than IES — same shape as IESBUILD-247 / `compu_bs5#665` that we closed. But Hitesh has the architectural context on whether Bootstrap 5 carousel customization is intentionally per-site or core. Once he weighs in, this becomes either a re-dispatch (with the v1.13 workflow producing the right outcome) or a confirmation of the current PR.

**Action:** Wait for Hitesh. No Symphony work needed in the interim.

---

## Item 13 — RC-cycle vs hotfix distinction (decided ignore)

**Status:** Explicitly decided NOT to implement. Recorded here so future planners don't re-propose it.

**Decision:** During the v1.13 plan for items 9–12 (commit `aee5206`), the engineer chose to drop both regex-based RC detection AND any `compuco_projects.yml` mirror logic for BASE_COMMIT. Core PRs always target the default branch. If a Compuclient release is mid-flight and the PR should land against the RC branch instead, the operator changes the PR base manually after `gh pr create`. Simpler workflow, accepted manual tax during release windows.

**Don't revisit unless:** the manual-RC-rebase tax becomes a regular friction point during release cycles.

---

## Future workstreams (mentioned in old v1.12 plan, not yet scheduled)

### Automated Gemini review-feedback loop

After `gh pr create`, Symphony's run ends. Gemini and other bot reviewers post inline comments within minutes. Today a human reads, evaluates, applies valid changes, replies, resolves threads, re-requests review. This loop is automatable:

- Trigger: after `gh pr create` succeeds, poll the PR for new Gemini inline comments (3-min wait + `gh api repos/.../pulls/.../comments`).
- Loop: read each unresolved comment → evaluate → if accepted, apply + commit + push + reply → resolve thread → re-tag `@gemini-code-assist review` → wait + repeat.
- Termination: no new comments (LGTM), OR N=3 rounds reached, OR only SUGGESTION-level remaining.
- Interaction with internal reviewer (step 12a): complementary. Internal catches before public exposure; Gemini loop addresses post-open feedback.

**Open question:** how to bound Gemini-suggestion rejection. If the agent rejects, it must post a reply with a clear technical reason (e.g. the `event.target !== this` case from IESBUILD-232). If it can't reason confidently, it flags and leaves the thread for a human.

**Estimated complexity:** medium. Would extend the per-ticket flow from "open PR and stop" to "open PR and converge". Probably its own plan batch.

### Multi-client QA-branch fan-out

When a core fix benefits multiple clients (IES + MM + CST + …), v1.13 currently pushes the QA branch only to the originating client. Multi-client propagation is a v1.13+ workstream — needs design for which clients get the fan-out, how to coordinate QA, how to handle per-client vendor-copy divergence.

### Status tracking for core PR merge → next Compuclient release

Symphony today doesn't track core PR merge timing. When a core PR merges and ships in the next Compuclient release, the originating client tickets stay in `agent:todo`'s aftermath state (no label, no auto-close) until the operator manually closes them as `fixed-in-core`. A future enhancement: a separate monitor watches core PRs whose origin is a client ticket, and notifies the operator when they merge.

---

## How to use this file

When planning the next batch:
1. Pick one or more items from the list.
2. Move them out of this file into a per-batch plan (e.g. `~/.claude/plans/v1.14-<feature>.md`).
3. Implement following the writing-plans + subagent-driven-development pattern.
4. After implementation lands, remove the item here (or mark `Status: Done in <commit>`).

The list is intentionally short — drop items aggressively once they no longer apply.
