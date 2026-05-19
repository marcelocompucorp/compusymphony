# Dashboard Improvements: Workflow Step Tracking + Pending Queue

**Date:** 2026-05-19
**Status:** Approved

## Summary

Two improvements to the web dashboard (Phoenix LiveView at `DashboardLive`):

1. **Workflow step tracking** — replace the vague "Agent update" column with a real step indicator showing which WORKFLOW.md step the agent is currently on, plus a progress bar.
2. **Pending queue visibility** — show issues that are eligible for dispatch but waiting for a free agent slot, with their position in the dispatch order.

## Constraints

- No extra LLM API calls. All new data comes from file I/O or existing in-memory state.
- No changes to the terminal dashboard (`StatusDashboard`) — web only.

---

## Feature 1: Workflow Step Tracking

### Agent side

One instruction added to WORKFLOW.md (in the instructions the agent follows): at the start of each numbered step, write the following file to its workspace root:

**File:** `<workspace_root>/<issue_identifier>/.symphony-status`

**Format:**
```json
{"step": 7, "total": 12, "label": "Run tests and verify"}
```

Fields:
- `step` — current step number (1-based integer)
- `total` — total number of steps (integer)
- `label` — step heading text (string, matches the WORKFLOW.md step title)

The agent controls the label — it copies the step heading from WORKFLOW.md. No summarization or token cost.

### Symphony side — reading the file

In `SymphonyElixirWeb.Presenter.running_entry_payload/1`, after building the existing fields, read `.symphony-status` for each running entry:

- Path: `Path.join([Config.workspace_root(), entry.issue_identifier, ".symphony-status"])`
- Parse as JSON. On any error (file missing, malformed JSON, wrong types), set `step_info: nil`.
- Valid result: `step_info: %{step: 7, total: 12, label: "Run tests and verify"}`

This read happens synchronously during snapshot projection. It is a local file read — no network, no tokens.

### Web dashboard — rendering

Replace the "Agent update" column with "Activity":

- If `step_info` is present: show step badge (`Step 7 / 12`), label text, and a row of pip indicators (filled for completed steps, highlighted for current, empty for remaining).
- If `step_info` is nil: fall back to the existing `last_message || last_event || "n/a"` display (same as today, so no regression).

Column header changes from "Agent update" → "Activity".

---

## Feature 2: Pending Queue

### Orchestrator state change

Add `pending: []` to `Orchestrator.State` (default empty list).

In `choose_issues/2` (called after each poll), after dispatching eligible issues, store the issues that are "would run next if a slot opened" as `state.pending`. These are issues that pass all checks in `should_dispatch_issue?` *except* the slot-availability checks (`available_slots > 0` and `state_slots_available?`). This excludes blocked issues, already-running issues, and already-claimed issues — only issues genuinely waiting for a free slot appear here.

Concretely: after the dispatch loop, filter the sorted candidate list to issues that satisfy:
- `candidate_issue?` (valid id/identifier/title/state, routable to worker, active state, not terminal)
- `!todo_issue_blocked_by_non_terminal?`
- `!MapSet.member?(claimed, issue.id)`
- `!Map.has_key?(running, issue.id)`

Store as `state.pending` in the same dispatch priority order (priority rank → created_at → identifier).

Clear `pending` to `[]` at the start of each poll cycle (stale data is worse than no data).

### Snapshot change

Add `pending` to the `:snapshot` reply in `handle_call(:snapshot, ...)`:

```elixir
pending:
  state.pending
  |> Enum.map(fn issue ->
    %{
      issue_id: issue.id,
      identifier: issue.identifier,
      title: issue.title,
      state: issue.state,
      priority: issue.priority,
      url: issue.url
    }
  end)
```

### Presenter change

In `state_payload/2`, add to the returned map:

```elixir
pending: Enum.map(snapshot.pending, &pending_entry_payload/1),
counts: %{
  running: ...,
  retrying: ...,
  queued: length(snapshot.pending)   # new
}
```

`pending_entry_payload/1` maps the snapshot pending entry to the same shape (no transformation needed beyond what the orchestrator already built).

### Web dashboard changes

**Metric cards:** Add a "Queued" card between "Retrying" and "Total tokens", showing `@payload.counts.queued`. Amber/yellow color to distinguish from Running (blue) and Retrying (red).

**New "Pending queue" section:** Add below "Running sessions", above "Retry queue".

Table columns:
- `#` — 1-based position (order from the list, which reflects dispatch priority)
- Issue — identifier + "link" anchor to issue URL if available
- Title — truncated to ~60 chars
- State — existing state badge styling
- Priority — priority badge (Urgent/High/Medium/Low mapped from integer 1–4; nil → no badge)

Empty state: "No issues are queued — all eligible issues are running or no new candidates found."

---

## Data Flow Summary

```
Poll cycle
  → fetch_candidate_issues()
  → filter + sort candidates
  → dispatch up to max_concurrent_agents
  → store remainder in state.pending

Snapshot request
  → read state.pending, state.running, state.retrying
  → for each running entry, read .symphony-status from workspace (file I/O)
  → build payload

LiveView push
  → render Activity column (step badge + pips, or fallback)
  → render Pending queue section
  → render Queued metric card
```

---

## Error Handling

- `.symphony-status` missing or unreadable → `step_info: nil` → fallback display. No crash.
- `.symphony-status` has unexpected JSON shape → `step_info: nil`. Validate that `step` and `total` are positive integers and `label` is a non-empty string before accepting.
- `pending` list is stale (poll hasn't run since last dispatch) → acceptable; it clears at next poll start.
- Snapshot timeout → existing error handling unchanged.

---

## What Is Not In Scope

- Terminal dashboard (`StatusDashboard`) — no changes.
- Step tracking for the retrying queue — entries there are not actively running agents.
- Historical step data or step completion timestamps.
- Workflow step tracking in the JSON API (`/api/v1/:identifier`).
