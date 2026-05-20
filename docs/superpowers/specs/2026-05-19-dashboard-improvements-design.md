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

One instruction added to WORKFLOW.md (in the instructions the agent follows): at the start of each numbered step, write the following file to the workspace root. The write happens **before** the step's work begins — including step 1, which is written before any other work in the session.

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

The agent writes this as a single atomic overwrite (write to a temp file, then rename). A partial read during an overwrite yields malformed JSON, which is handled safely by `step_info: nil` — no retry logic needed.

### Symphony side — reading the file

In `SymphonyElixirWeb.Presenter.running_entry_payload/1`, after building the existing fields, read `.symphony-status` for each running entry:

- Path: `Path.join([Config.workspace_root(), entry.issue_identifier, ".symphony-status"])`
- Parse as JSON. On any error (file missing, malformed JSON, wrong types, partial read), set `step_info: nil`.
- Validate before accepting: `step` and `total` are positive integers, `label` is a non-empty string. Anything else → `step_info: nil`.
- Valid result: `step_info: %{step: 7, total: 12, label: "Run tests and verify"}`

This read happens synchronously during snapshot projection. It is a local file read — no network, no tokens.

If `Config.workspace_root()` is not explicitly configured, it returns a system temp path and `.symphony-status` will never exist, so `step_info` will always be `nil` (silent fallback, no crash). This is the expected degraded behaviour for unconfigured deployments.

### Web dashboard — rendering

Replace the "Agent update" column with "Activity":

- If `step_info` is present: show step badge (`Step 7 / 12`), label text, and a row of pip indicators (filled for completed steps, highlighted for current, empty for remaining). Cap pip rendering at 15 total — if `total > 15`, replace the pip row with a plain fraction string (`7 / 12`) to avoid cell overflow.
- If `step_info` is nil: fall back to the existing `last_message || last_event || "n/a"` display (same as today, so no regression).

Column header changes from "Agent update" → "Activity".

---

## Feature 2: Pending Queue

### Orchestrator state change

Add `pending: []` to `Orchestrator.State` (default empty list).

**Where pending is computed:** In `maybe_dispatch/1`, unconditionally after `fetch_candidate_issues/0` succeeds — regardless of whether `available_slots > 0`. This is critical: the most useful case for the pending queue is when all slots are full, which is exactly when `choose_issues/2` is never called.

Compute `pending` from the sorted candidate list by filtering to issues that satisfy all of the following against the **pre-dispatch** `state.running` and `state.claimed` (not the post-dispatch state):
- `candidate_issue?` (valid id/identifier/title/state, routable to worker, active state, not terminal)
- `!todo_issue_blocked_by_non_terminal?`
- `!MapSet.member?(state.claimed, issue.id)`
- `!Map.has_key?(state.running, issue.id)`

These are issues that would be dispatched immediately if a slot opened. Store as `state.pending` in dispatch priority order (priority rank → created_at → identifier).

Clear `pending` to `[]` at the start of each poll cycle, before `fetch_candidate_issues/0` is called (stale data is worse than no data).

Implementation sketch for `maybe_dispatch/1`:

```elixir
defp maybe_dispatch(%State{} = state) do
  state = reconcile_running_issues(state)
  state = %{state | pending: []}   # clear at poll start

  with :ok <- Config.validate!(),
       {:ok, issues} <- Tracker.fetch_candidate_issues() do
    sorted = sort_issues_for_dispatch(issues)
    pending = compute_pending(sorted, state)     # always compute
    state = %{state | pending: pending}

    if available_slots(state) > 0 do
      choose_issues(sorted, state)               # dispatches, may grow running
    else
      state
    end
  else
    ...
  end
end
```

`compute_pending/2` applies the four filters above against the pre-dispatch state.

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

In `state_payload/2`, the **success branch** adds:

```elixir
pending: Enum.map(snapshot.pending, &pending_entry_payload/1),
counts: %{
  running: length(snapshot.running),
  retrying: length(snapshot.retrying),
  queued: length(snapshot.pending)
}
```

`pending_entry_payload/1` is a named private function that passes the map through unchanged (defined as a named function rather than `& &1` for future extensibility).

The **error branches** (`:timeout` and `:unavailable`) must also include `pending: []` and `counts: %{running: 0, retrying: 0, queued: 0}` to prevent template `KeyError` crashes. The `DashboardLive` template must not assume the success shape.

`pending` will appear in any endpoint that calls `state_payload/2` (e.g. `/api/v1/status` if it exists). This is intentional — it is additive and non-breaking for JSON consumers.

### Web dashboard changes

**Metric cards:** Add a "Queued" card between "Retrying" and "Total tokens", showing `@payload.counts.queued`. Amber/yellow color to distinguish from Running (blue) and Retrying (red).

**New "Pending queue" section:** Add below "Running sessions", above "Retry queue".

Table columns:
- `#` — 1-based position (order from the list, which reflects dispatch priority)
- Issue — identifier + link anchor to issue URL if available
- Title — truncated to ~60 chars
- State — existing state badge styling
- Priority — priority badge (Urgent / High / Medium / Low mapped from integer 1–4; `nil` or out-of-range integer → no badge rendered)

Empty state: "No issues are queued — all eligible issues are running or no new candidates found."

---

## Data Flow Summary

```
Poll cycle start
  → clear state.pending = []
  → reconcile_running_issues()
  → fetch_candidate_issues()
  → sort candidates
  → compute_pending() against pre-dispatch state  ← always runs
  → store state.pending
  → if slots available: choose_issues() → dispatch

Snapshot request
  → read state.pending, state.running, state.retrying
  → for each running entry: read .symphony-status from workspace (file I/O)
  → build payload (pending: [] on error/timeout branches)

LiveView push
  → render Activity column (step badge + pips, or fallback)
  → render Pending queue section
  → render Queued metric card
```

---

## Error Handling

- `.symphony-status` missing or unreadable → `step_info: nil` → fallback display. No crash.
- `.symphony-status` partial read (mid-write) → malformed JSON → `step_info: nil`. No retry needed.
- `.symphony-status` has unexpected JSON shape → `step_info: nil`. Validate `step`/`total` are positive integers and `label` is non-empty string before accepting.
- `workspace_root` unconfigured → file path resolves to temp dir → `step_info` always nil → silent fallback. Expected for unconfigured deployments.
- `pending` list stale → cleared at next poll start. Acceptable gap.
- Snapshot timeout or unavailable → `pending: []`, `counts.queued: 0` in payload. Template renders empty queue section, no crash.

---

## What Is Not In Scope

- Terminal dashboard (`StatusDashboard`) — no changes.
- Step tracking for the retrying queue — entries there are not actively running agents.
- Historical step data or step completion timestamps.
- Workflow step tracking in the per-issue JSON API (`/api/v1/:identifier`).
