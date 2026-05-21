# Symphony Backlog

Engineering feedback items deferred from the v1.13 batch, pending a future planning round. Item numbers continue from the v1.13 feedback list (items 1–16); new items start at 17.

---

## Item 19 — assert_bug_fixed produces false negatives on transition-based UI changes

**Status:** Sub-fixes (a) and (d) landed in v1.13.2 (`<follow-up commit>`). Sub-fixes (b) and (c) deferred — add when a real ticket needs them. Surfaced by IESBUILD-247 (2026-05-21).

**What landed in v1.13.2:**
- (a) `visual-repro.md` §8's "`assert_bug_fixed(page)` — inverse assertion" section now includes a dedicated "Async state assertions" sub-section that prescribes Playwright's retrying `expect(...).to_be_hidden(timeout=10000)` / `to_be_visible(timeout=10000)` / `to_have_class(...)` / `to_have_text(...)` patterns for interaction-driven async state changes. Default timeout 10 s covers Bootstrap fades (~500 ms), modal animations (~300 ms), and 5 s auto-advance carousels with headroom. Carve-out documents legitimate `wait_for_timeout` uses (CSS paint settle after `add_style_tag`, Jenkins polls, network-idle waits). Cross-referenced from §3 so `assert_bug_reproduced` gets the same guidance symmetrically.
- (d) `code-reviewer.md` "Visual-repro invariants" section now has invariant 6: scan inside `assert_bug_*` functions for the `wait_for_timeout(N)` + immediate `is_visible()` / `class_list` / `text_content()==` anti-pattern. WARNING-level finding suggesting migration to retrying `expect`. Scoped to assertion functions only; legitimate `wait_for_timeout` uses are explicitly carved out.

**What's still deferred (revisit when a ticket needs them):**
- (b) Auto-advancing UI explicit cycle-length wait. `expect(...).to_have_text(..., timeout=10000)` already covers IESBUILD-232's 5 s carousel because the 10 s default ≥ one cycle + headroom. Sub-fix (a) subsumes (b) for the auto-advance case until proven otherwise.
- (c) Multi-signal verification (aria-expanded + display + visibility — at least 2 of 3). Useful when retrying `expect` proves insufficient for some specific class of failure; not needed yet.

**Original problem statement and failure case (preserved for context):**

**Problem.** The Phase B `assert_bug_fixed` Playwright assertion at WORKFLOW.md step 12b-bis can return false-negative — reporting the bug as still present when it has actually been fixed — for UI changes that involve CSS transitions or animations. The Core PR gate (item 12) then blocks a legitimately working fix.

**Concrete failure case.** IESBUILD-247's deployed fix actually works (user confirmed manually in the browser: clicking outside the login popup closes it). But the agent's `assert_bug_fixed` kept reporting `popup.is_visible() == True` after the outside click, four times in a row. Looking at `repro_devsite_after.py`:

```python
page.mouse.click(target_x, target_y)
page.wait_for_timeout(600)   # 600ms wait
# ...
assert not popup.is_visible(), "Expected ... HIDDEN after outside click"
```

The popup likely has a CSS transition (opacity fade or height collapse, common Bootstrap 5 pattern) that takes ~500–1000ms. At the 600ms check, the popup is mid-transition: opacity > 0 OR computed `display` not yet `none`. Playwright's `is_visible()` returns True. The fix is correct; the assertion's timing window is wrong.

**Why this matters.** v1.13 item 12 made the Core PR gate first-class — `assert_bug_fixed` failure blocks `gh pr create`. The stricter the gate, the more painful a false negative becomes:
- Blocks the run with `AGENT_DONE = blocked-verify`
- Combined with item 18 (no actual stop), kicks off an indefinite Phase B retry loop
- Operator has to manually inspect, confirm the fix works, and either open the PR by hand or relax the test

The IESBUILD-247 case wasted ~70 min of CI + 4 dev-site re-releases before the loop was broken. Most of that was item 18's fault, but the root trigger was item 19's false negative.

**Proposed fix (sketch for v1.14 plan stage).**

Several layers, in increasing order of effort:

1. **Generous default wait window.** Replace fixed `page.wait_for_timeout(600)` with a polling loop: try `is_visible()` once a second for up to 5–10 seconds; assertion passes as soon as it returns False. This handles transitions up to 5–10s without flakiness, and adds minimal time when the assertion passes quickly.

2. **Use Playwright's `expect(...).to_be_hidden()` with timeout.** Playwright has built-in retrying assertions (`expect(locator).to_be_hidden(timeout=10000)`) designed exactly for this. Migrate `assert not is_visible()` → `expect(popup).to_be_hidden(timeout=10000)`. This is the idiomatic Playwright pattern.

3. **Multi-signal verification.** Check `aria-expanded`, `display`, AND visual visibility — at least two of three must agree on "hidden" before considering the popup closed. Reduces single-source flakiness.

4. **Reviewer guidance.** In `prompts/code-reviewer.md`, add a check for "does the Phase B `assert_bug_fixed` use `wait_for_timeout(<fixed-ms>)` followed by an immediate is_visible() check? If so, suggest Playwright's retrying `expect(...).to_be_hidden(timeout=...)` pattern." Catches the anti-pattern before it ships.

5. **Cross-reference item 17.** Both items are about gate reliability. Item 17 is "wrong bug reproduced" (false-positive direction — agent claims success on wrong symptom). Item 19 is "right fix not detected" (false-negative direction). Together they bound the gate's failure modes. A v1.14 plan that addresses gate-reliability holistically might cover both.

**Open questions for v1.14 plan stage:**
- Should the default `assert_bug_fixed` template in `prompts/visual-repro.md` switch to `expect(...).to_be_hidden(timeout=10000)`? If yes, what's the right default timeout — 5s, 10s, 15s?
- Are there other Playwright assertion patterns in `prompts/visual-repro.md` that have the same fixed-timeout anti-pattern?
- How should an operator override / relax the gate for a known false-negative? A `agent:phase-b-skip` label? A `## Comments` annotation that the reviewer reads?

**Action for IESBUILD-247:** The fix on `agent/IESBUILD-247-fix` (compu_bs5 branch) and `qa-IESBUILD-247` (ies branch) is correct and deployed. To complete this ticket without re-dispatch:
1. Manually open the core PR: `gh pr create --repo compucorp/compu_bs5 --base master --head agent/IESBUILD-247-fix`
2. Post Jira comment explaining the false negative and the manual completion path.
3. Don't re-apply `agent:todo` until item 19 (and ideally 17 + 18) is in place.

---

## Item 18 — Agent must STOP after writing AGENT_DONE

**Status:** Partially landed (Fix A — bookkeeping). Orchestrator-side enforcement deferred. Surfaced by IESBUILD-247 (2026-05-21).

**Update 2026-05-21:** Original framing oversimplified. Reconstructing IESBUILD-247's actual sequence revealed three distinct nuances:

1. **AGENT_DONE was written prematurely** as `blocked-verify` when Phase B first failed. Agent then recovered, fixed its own bug, re-deployed, succeeded, opened PR `compu_bs5#667`, and posted success Jira comment — but the stale `AGENT_DONE = blocked-verify` was never updated.
2. **Step 14 (label removal) was skipped on the success path.** Agent likely interpreted its own earlier `blocked-verify` write as "I'm in blocked state, don't remove the label." Stale-signal-driven decision.
3. **Process hung ~40 min after success** without producing further artifacts; operator had to SIGTERM.

**Fix A landed (commit `38bd4dc`):** "Defer AGENT_DONE writes to step 15." All mid-workflow gates now route to step 15 with a prefix (`blocked`, `blocked-verify`, `blocked-review`) rather than writing `AGENT_DONE` directly. Step 15 is the single write site for the standard Routine. Includes a one-retry cap on Phase B recovery so the agent has a documented bound on iterations. Solves nuances 1 and 2 transitively.

**Still deferred (revisit if Fix A alone doesn't suffice):**

- **Orchestrator-side enforcement.** Symphony Elixir process polls `<workspace>/AGENT_DONE` and SIGTERMs the agent on appearance. This is the load-bearing fix for nuance 3 (process hang). Not implemented yet — relying on the agent honouring step 15's "stop" prose discipline. If the next blocked-or-recover case shows another hang, this becomes priority.
- **Broader Phase B trigger cap.** Today's one-retry cap is in the recovery paragraph; a hard orchestrator-side or repro-helper-side cap would catch any agent that ignores the prose cap. Tied to BACKLOG item 19's gate-reliability work.

**Reference incident:** PR `compu_bs5#667` is open and correct despite the messy run. The agent's own diagnostic in Jira comment `#269946` correctly identified the `$(document).once(...)` Drupal 7 issue; the recovery commit `19e7e61` (closure-flag pattern) fixed it. Net outcome was good; only the bookkeeping was broken.

---

## Original framing (for historical context — superseded by Fix A above)

**Problem.** WORKFLOW.md step 15 reads *"Write AGENT_DONE and stop."* The agent reliably writes the AGENT_DONE file but doesn't actually exit the Claude Code session afterwards. Instead it keeps processing — typically retrying whatever caused the blocker.

**Concrete failure case.** IESBUILD-247 Phase B's `assert_bug_fixed` failed (root cause: `$(document).once(...)` is a no-op in Drupal 7, see backlog item context). The agent correctly wrote:

```
AGENT_DONE = blocked-verify 2026-05-20T23:17:29Z IESBUILD-247
```

Then it did NOT stop. Over the next ~70 minutes it triggered **three additional Phase B Jenkins builds** (`_Release Dev Site` #3808, #3809, #3810), each re-deploying the same broken `agent-IESBUILD-247-fix` tag, running Playwright against the dev site, observing the same `assert_bug_fixed` failure, and looping. Symphony's dashboard kept showing the session as "Running" (57m / 1 turn) because the Claude Code process never exited. Operator had to manually `kill 71124 71130` and `POST .../stop` on the in-flight Jenkins build to break the loop.

**Why this happens.** Step 15 is a prose instruction telling the agent to stop. There's no programmatic enforcement: writing AGENT_DONE doesn't trigger any side effect that would actually terminate the process. The agent, in its loop, sees the failed assertion and self-prompts to retry — the same training-time inclination that makes agents persist through obstacles.

**Compounding factor: Phase B is cheap to re-trigger.** The `trigger_release_devsite` helper is a single Jenkins POST that returns quickly. Each retry costs ~5 min of Jenkins time + ~2 min of Playwright assertion. The agent doesn't perceive this as "I'm in a loop" — to it, each retry is "trying the workflow again from where it failed".

**Proposed fix (sketch — for a v1.14 plan to refine).**

Two layers, defense in depth:

1. **WORKFLOW.md step 15 hardening.** Replace the prose "write AGENT_DONE and stop" with an explicit terminal sequence the agent runs as one atomic action:
   ```bash
   echo "blocked-verify $(date -u +%Y-%m-%dT%H:%M:%SZ) <TICKET>" > AGENT_DONE
   exit
   ```
   The shell `exit` (or its tool-call equivalent: don't make any further tool calls; immediately produce a terminal message containing only the AGENT_DONE content) is the stop. This makes the stop a single observable action rather than a state the agent has to remember to leave.

2. **Symphony orchestrator-side enforcement.** Have the Elixir orchestrator watch for AGENT_DONE in the workspace. When AGENT_DONE appears, kill the agent process (SIGTERM with 30s timeout, then SIGKILL). This catches the case where the agent writes the file and then misbehaves. Implementation: `WorkflowStore`-style file polling on `<workspace>/AGENT_DONE`. Could share the same 1-second poll interval.

The orchestrator-side enforcement is the load-bearing fix — relying only on agent self-discipline is the failure mode we just observed.

**Why this matters now.** v1.13's Core PR gate (item 12) WORKED for IESBUILD-247 — it correctly blocked the bad PR. But the gate's effectiveness was diluted by the agent then quietly burning ~70 min of CI time, dev-site re-deploys, and Anthropic API tokens in a retry loop. If item 18 isn't fixed, every `blocked-verify` outcome becomes a runaway retry until a human notices.

**Open questions for v1.14 plan stage:**
- Does Claude Code's `--print` mode support a clean way to signal "session done, exit cleanly"? If yes, prefer that over external SIGTERM.
- Should the kill window after AGENT_DONE be the same for all sentinels (success, blocked-verify, blocked-review, blocked), or differ? E.g. maybe `success` doesn't need a kill (Jira-comment-then-stop is the natural tail), but `blocked-*` does (the agent has nothing more it should be doing).
- Is there a way to make this fix backwards-compatible with in-flight pre-v1.14 sessions, or does it require a clean cutover?

**Action for IESBUILD-247:** processes killed, retry Jenkins build aborted, ticket left in `blocked-verify` state with no `agent:todo` (operator removed). Next dispatch should wait until item 18 is in place, otherwise the same loop can happen again the moment a `blocked-verify` is hit.

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
