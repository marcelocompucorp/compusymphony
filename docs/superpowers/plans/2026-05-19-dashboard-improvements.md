# Dashboard Improvements: Workflow Step Tracking + Pending Queue

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pending queue section and a real workflow step indicator to the Symphony web dashboard, with zero extra LLM API calls.

**Architecture:** The orchestrator computes a `pending` list (issues waiting for a free slot) after each poll and stores it in State; the presenter exposes it in the snapshot payload alongside `step_info` read from a per-session `.symphony-status` file the agent writes to its workspace. The LiveView dashboard renders a new "Pending queue" section, a "Queued" metric card, and replaces the vague "Agent update" column with an "Activity" column showing a step badge + progress pips (falling back to the last event when no status file exists).

**Tech Stack:** Elixir, Phoenix LiveView, Jason (JSON), ExUnit

---

## File Map

| File | Change |
|------|--------|
| `elixir/lib/symphony_elixir/orchestrator.ex` | Add `pending: []` to State, add `compute_pending/2`, restructure `maybe_dispatch/1`, expose `pending` in `:snapshot` reply |
| `elixir/lib/symphony_elixir_web/presenter.ex` | Add `read_step_info/1`, update `running_entry_payload/1` to include `step_info`, add `pending_entry_payload/1`, update `state_payload/2` (success + error branches) |
| `elixir/lib/symphony_elixir_web/live/dashboard_live.ex` | Add Queued metric card, replace "Agent update" → "Activity" column with step rendering, add Pending queue section, add helper functions |
| `elixir/priv/static/dashboard.css` | Add pip styles (`.pip`, `.pip-done`, `.pip-active`, `.pip-empty`, `.step-badge`, `.step-stack`, `.priority-badge` variants) |
| `elixir/WORKFLOW.md` | Add instruction to write `.symphony-status` at each step |
| `elixir/test/symphony_elixir/orchestrator_status_test.exs` | Tests for `compute_pending/2` logic and pending in snapshot |
| `elixir/test/symphony_elixir/presenter_step_info_test.exs` | Tests for `read_step_info/1` and `state_payload/2` error branch changes |
| `elixir/test/symphony_elixir_web/dashboard_live_helpers_test.exs` | Unit tests for `pip_class/3`, `priority_label/1`, `priority_badge_class/1`, `truncate_title/1` |

---

## Task 1: Orchestrator — pending state + compute_pending

**Files:**
- Modify: `elixir/lib/symphony_elixir/orchestrator.ex`
- Test: `elixir/test/symphony_elixir/orchestrator_status_test.exs`

- [ ] **Step 1.1: Write failing tests for compute_pending**

Add these tests to `elixir/test/symphony_elixir/orchestrator_status_test.exs` (inside the existing module, after the last test):

```elixir
describe "compute_pending/2" do
  test "returns issues that are not running, not claimed, not blocked, and pass candidate checks" do
    issue_a = %Issue{id: "a", identifier: "MT-10", title: "A", state: "To Do",
                     assigned_to_worker: true}
    issue_b = %Issue{id: "b", identifier: "MT-11", title: "B", state: "To Do",
                     assigned_to_worker: true}
    issue_c = %Issue{id: "c", identifier: "MT-12", title: "C", state: "To Do",
                     assigned_to_worker: true}

    state = %Orchestrator.State{
      running: %{"a" => %{identifier: "MT-10", issue: issue_a}},
      claimed: MapSet.new(["b"]),
      retry_attempts: %{},
      max_concurrent_agents: 1
    }

    # a is running, b is claimed, c is neither — only c should be pending
    result = Orchestrator.compute_pending_for_test([issue_a, issue_b, issue_c], state)
    assert length(result) == 1
    assert hd(result).id == "c"
  end

  test "returns empty list when all issues are running or claimed" do
    issue_a = %Issue{id: "a", identifier: "MT-10", title: "A", state: "To Do",
                     assigned_to_worker: true}

    state = %Orchestrator.State{
      running: %{"a" => %{identifier: "MT-10", issue: issue_a}},
      claimed: MapSet.new(),
      retry_attempts: %{},
      max_concurrent_agents: 1
    }

    result = Orchestrator.compute_pending_for_test([issue_a], state)
    assert result == []
  end

  test "excludes blocked todo issues" do
    blocker = %Issue{id: "x", identifier: "MT-99", title: "Blocker", state: "In Progress",
                     assigned_to_worker: true}
    blocked = %Issue{id: "b", identifier: "MT-11", title: "Blocked", state: "To Do",
                     assigned_to_worker: true, blocked_by: [%{state: "In Progress"}]}

    state = %Orchestrator.State{
      running: %{},
      claimed: MapSet.new(),
      retry_attempts: %{},
      max_concurrent_agents: 2
    }

    result = Orchestrator.compute_pending_for_test([blocker, blocked], state)
    # blocked todo should be excluded; blocker (In Progress) also excluded (not "To Do" active state)
    # Actually depends on active_states config — verify none pass if active_states = ["To Do"]
    # blocker state "In Progress" is also active, so it passes candidate check but isn't blocked
    # This test verifies "blocked" is excluded
    identifiers = Enum.map(result, & &1.identifier)
    refute "MT-11" in identifiers
  end
end
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd elixir && mix test test/symphony_elixir/orchestrator_status_test.exs --seed 0 2>&1 | tail -20
```

Expected: compile error or `Orchestrator.compute_pending_for_test/2 is undefined`.

- [ ] **Step 1.3: Add `pending` to State struct**

In `elixir/lib/symphony_elixir/orchestrator.ex`, update the `State` defstruct:

```elixir
defstruct [
  :poll_interval_ms,
  :max_concurrent_agents,
  :next_poll_due_at_ms,
  :poll_check_in_progress,
  running: %{},
  completed: MapSet.new(),
  claimed: MapSet.new(),
  retry_attempts: %{},
  pending: [],                 # ← new
  agent_totals: nil,
  agent_rate_limits: nil
]
```

- [ ] **Step 1.4: Add `compute_pending/2` and its test-facing export**

Add these two functions to the private section of `orchestrator.ex` (near the other private dispatch helpers, after `sort_issues_for_dispatch/1`):

```elixir
defp compute_pending(sorted_issues, %State{} = state) do
  active_states = active_state_set()
  terminal_states = terminal_state_set()

  Enum.filter(sorted_issues, fn issue ->
    candidate_issue?(issue, active_states, terminal_states) and
      !todo_issue_blocked_by_non_terminal?(issue, terminal_states) and
      !MapSet.member?(state.claimed, issue.id) and
      !Map.has_key?(state.running, issue.id)
  end)
end

@doc false
@spec compute_pending_for_test([Issue.t()], term()) :: [Issue.t()]
def compute_pending_for_test(issues, state), do: compute_pending(issues, state)
```

- [ ] **Step 1.5: Restructure `maybe_dispatch/1`**

Replace the existing `maybe_dispatch/1` with:

```elixir
defp maybe_dispatch(%State{} = state) do
  state = reconcile_running_issues(state)
  state = %{state | pending: []}

  with :ok <- Config.validate!(),
       {:ok, issues} <- Tracker.fetch_candidate_issues() do
    sorted = sort_issues_for_dispatch(issues)
    state = %{state | pending: compute_pending(sorted, state)}

    if available_slots(state) > 0 do
      choose_issues(sorted, state)
    else
      state
    end
  else
    {:error, reason} when is_binary(reason) ->
      Logger.error(reason)
      state

    {:error, reason} ->
      Logger.error("Failed to fetch from tracker: #{inspect(reason)}")
      state
  end
end
```

- [ ] **Step 1.6: Update `choose_issues/2` to accept pre-sorted list**

Replace the existing `choose_issues/2` with:

```elixir
defp choose_issues(sorted_issues, state) do
  active_states = active_state_set()
  terminal_states = terminal_state_set()

  Enum.reduce(sorted_issues, state, fn issue, state_acc ->
    if should_dispatch_issue?(issue, state_acc, active_states, terminal_states) do
      dispatch_issue(state_acc, issue)
    else
      state_acc
    end
  end)
end
```

(Removed the `sort_issues_for_dispatch()` call inside — sorting now happens in `maybe_dispatch/1`.)

- [ ] **Step 1.7: Expose `pending` in `:snapshot` reply**

In `handle_call(:snapshot, _from, state)`, add `pending` to the reply map. The full `:snapshot` reply block should now be:

```elixir
{:reply,
 %{
   running: running,
   retrying: retrying,
   pending: Enum.map(state.pending, fn issue ->
     %{
       issue_id: issue.id,
       identifier: issue.identifier,
       title: issue.title,
       state: issue.state,
       priority: issue.priority,
       url: issue.url
     }
   end),
   agent_totals: state.agent_totals,
   rate_limits: Map.get(state, :agent_rate_limits),
   polling: %{
     checking?: state.poll_check_in_progress == true,
     next_poll_in_ms: next_poll_in_ms(state.next_poll_due_at_ms, now_ms),
     poll_interval_ms: state.poll_interval_ms
   }
 }, state}
```

- [ ] **Step 1.8: Run tests**

```bash
cd elixir && mix test test/symphony_elixir/orchestrator_status_test.exs --seed 0 2>&1 | tail -30
```

Expected: all tests pass, including the new `compute_pending` tests.

- [ ] **Step 1.9: Commit**

```bash
cd elixir && git add lib/symphony_elixir/orchestrator.ex test/symphony_elixir/orchestrator_status_test.exs
git commit -m "feat: orchestrator tracks pending queue after each poll cycle"
```

---

## Task 2: Presenter — pending payload + step_info

**Files:**
- Modify: `elixir/lib/symphony_elixir_web/presenter.ex`
- Create: `elixir/test/symphony_elixir/presenter_step_info_test.exs`

- [ ] **Step 2.1: Write failing tests**

Create `elixir/test/symphony_elixir/presenter_step_info_test.exs`:

```elixir
defmodule SymphonyElixir.PresenterStepInfoTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixirWeb.Presenter

  describe "step_info in running_entry_payload" do
    test "returns step_info when .symphony-status is valid" do
      workspace_root =
        Path.join(System.tmp_dir!(), "symphony-step-info-#{System.unique_integer([:positive])}")

      identifier = "MT-200"
      workspace_path = Path.join(workspace_root, identifier)
      File.mkdir_p!(workspace_path)
      status_path = Path.join(workspace_path, ".symphony-status")
      File.write!(status_path, ~s({"step": 3, "total": 10, "label": "Run tests"}))

      prev = Application.get_env(:symphony_elixir, :workspace_root)
      Application.put_env(:symphony_elixir, :workspace_root, workspace_root)

      on_exit(fn ->
        if is_nil(prev),
          do: Application.delete_env(:symphony_elixir, :workspace_root),
          else: Application.put_env(:symphony_elixir, :workspace_root, prev)

        File.rm_rf(workspace_root)
      end)

      entry = running_snapshot_entry(identifier)
      payload = Presenter.running_entry_payload_for_test(entry)

      assert payload.step_info == %{step: 3, total: 10, label: "Run tests"}
    end

    test "returns nil step_info when .symphony-status is absent" do
      workspace_root =
        Path.join(System.tmp_dir!(), "symphony-step-info-#{System.unique_integer([:positive])}")

      File.mkdir_p!(workspace_root)

      prev = Application.get_env(:symphony_elixir, :workspace_root)
      Application.put_env(:symphony_elixir, :workspace_root, workspace_root)

      on_exit(fn ->
        if is_nil(prev),
          do: Application.delete_env(:symphony_elixir, :workspace_root),
          else: Application.put_env(:symphony_elixir, :workspace_root, prev)

        File.rm_rf(workspace_root)
      end)

      entry = running_snapshot_entry("MT-201")
      payload = Presenter.running_entry_payload_for_test(entry)

      assert payload.step_info == nil
    end

    test "returns nil step_info when .symphony-status contains invalid JSON" do
      workspace_root =
        Path.join(System.tmp_dir!(), "symphony-step-info-#{System.unique_integer([:positive])}")

      identifier = "MT-202"
      workspace_path = Path.join(workspace_root, identifier)
      File.mkdir_p!(workspace_path)
      File.write!(Path.join(workspace_path, ".symphony-status"), "not json {{")

      prev = Application.get_env(:symphony_elixir, :workspace_root)
      Application.put_env(:symphony_elixir, :workspace_root, workspace_root)

      on_exit(fn ->
        if is_nil(prev),
          do: Application.delete_env(:symphony_elixir, :workspace_root),
          else: Application.put_env(:symphony_elixir, :workspace_root, prev)

        File.rm_rf(workspace_root)
      end)

      entry = running_snapshot_entry(identifier)
      payload = Presenter.running_entry_payload_for_test(entry)

      assert payload.step_info == nil
    end

    test "returns nil step_info when fields have wrong types" do
      workspace_root =
        Path.join(System.tmp_dir!(), "symphony-step-info-#{System.unique_integer([:positive])}")

      identifier = "MT-203"
      workspace_path = Path.join(workspace_root, identifier)
      File.mkdir_p!(workspace_path)
      # step is a string, not integer
      File.write!(Path.join(workspace_path, ".symphony-status"),
        ~s({"step": "three", "total": 10, "label": "Run tests"}))

      prev = Application.get_env(:symphony_elixir, :workspace_root)
      Application.put_env(:symphony_elixir, :workspace_root, workspace_root)

      on_exit(fn ->
        if is_nil(prev),
          do: Application.delete_env(:symphony_elixir, :workspace_root),
          else: Application.put_env(:symphony_elixir, :workspace_root, prev)

        File.rm_rf(workspace_root)
      end)

      entry = running_snapshot_entry(identifier)
      payload = Presenter.running_entry_payload_for_test(entry)

      assert payload.step_info == nil
    end
  end

  describe "state_payload/2 error branches include pending and counts" do
    test "timeout branch includes pending: [] and counts.queued: 0" do
      server_name = Module.concat(__MODULE__, :TimeoutServer)
      parent = self()

      pid =
        spawn(fn ->
          Process.register(self(), server_name)
          send(parent, :ready)

          receive do
            :stop -> :ok
          end
        end)

      assert_receive :ready, 1_000

      payload = Presenter.state_payload(server_name, 10)
      assert payload.pending == []
      assert payload.counts.queued == 0
      assert payload.error.code == "snapshot_timeout"

      send(pid, :stop)
    end
  end

  defp running_snapshot_entry(identifier) do
    %{
      issue_id: "issue-#{identifier}",
      identifier: identifier,
      state: "In Progress",
      session_id: nil,
      turn_count: 0,
      last_event: nil,
      last_message: nil,
      started_at: nil,
      last_event_at: nil,
      tokens: %{input_tokens: 0, output_tokens: 0, total_tokens: 0},
      agent_input_tokens: 0,
      agent_output_tokens: 0,
      agent_total_tokens: 0,
      last_codex_event: nil,
      last_codex_message: nil,
      last_codex_timestamp: nil
    }
  end
end
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
cd elixir && mix test test/symphony_elixir/presenter_step_info_test.exs --seed 0 2>&1 | tail -20
```

Expected: compile errors — `running_entry_payload_for_test/1` not defined.

- [ ] **Step 2.3: Add `read_step_info/1` to Presenter**

Add these private functions to `elixir/lib/symphony_elixir_web/presenter.ex`:

```elixir
defp read_step_info(identifier) when is_binary(identifier) do
  path = Path.join([Config.workspace_root(), identifier, ".symphony-status"])

  with {:ok, content} <- File.read(path),
       {:ok, decoded} <- Jason.decode(content),
       %{"step" => step, "total" => total, "label" => label} <- decoded,
       true <- is_integer(step) and step > 0,
       true <- is_integer(total) and total > 0,
       true <- is_binary(label) and label != "" do
    %{step: step, total: total, label: label}
  else
    _ -> nil
  end
end

defp read_step_info(_identifier), do: nil
```

Also add the alias at the top of the module (alongside the existing `alias SymphonyElixir.{Config, Orchestrator, StatusDashboard}`):

```elixir
alias SymphonyElixir.{Config, Orchestrator, StatusDashboard}
```

(Config is already there from `running_issue_payload/1` path construction — just confirm it's present.)

- [ ] **Step 2.4: Update `running_entry_payload/1` to include `step_info`**

Replace the existing `running_entry_payload/1`:

```elixir
defp running_entry_payload(entry) do
  %{
    issue_id: entry.issue_id,
    issue_identifier: entry.identifier,
    state: entry.state,
    session_id: entry.session_id,
    turn_count: Map.get(entry, :turn_count, 0),
    last_event: entry.last_codex_event,
    last_message: summarize_message(entry.last_codex_message),
    started_at: iso8601(entry.started_at),
    last_event_at: iso8601(entry.last_codex_timestamp),
    tokens: %{
      input_tokens: entry.agent_input_tokens,
      output_tokens: entry.agent_output_tokens,
      total_tokens: entry.agent_total_tokens
    },
    step_info: read_step_info(entry.identifier)
  }
end
```

Add a test-facing export (after the `running_entry_payload/1` definition):

```elixir
@doc false
@spec running_entry_payload_for_test(map()) :: map()
def running_entry_payload_for_test(entry), do: running_entry_payload(entry)
```

- [ ] **Step 2.5: Add `pending_entry_payload/1`**

Add this private function after `retry_entry_payload/1`:

```elixir
defp pending_entry_payload(entry) do
  %{
    issue_id: entry.issue_id,
    identifier: entry.identifier,
    title: entry.title,
    state: entry.state,
    priority: entry.priority,
    url: entry.url
  }
end
```

- [ ] **Step 2.6: Update `state_payload/2`**

Replace the full `state_payload/2` function:

```elixir
@spec state_payload(GenServer.name(), timeout()) :: map()
def state_payload(orchestrator, snapshot_timeout_ms) do
  generated_at = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

  case Orchestrator.snapshot(orchestrator, snapshot_timeout_ms) do
    %{} = snapshot ->
      pending = Map.get(snapshot, :pending, [])

      %{
        generated_at: generated_at,
        counts: %{
          running: length(snapshot.running),
          retrying: length(snapshot.retrying),
          queued: length(pending)
        },
        running: Enum.map(snapshot.running, &running_entry_payload/1),
        retrying: Enum.map(snapshot.retrying, &retry_entry_payload/1),
        pending: Enum.map(pending, &pending_entry_payload/1),
        agent_totals: snapshot.agent_totals,
        rate_limits: snapshot.rate_limits
      }

    :timeout ->
      %{
        generated_at: generated_at,
        counts: %{running: 0, retrying: 0, queued: 0},
        pending: [],
        error: %{code: "snapshot_timeout", message: "Snapshot timed out"}
      }

    :unavailable ->
      %{
        generated_at: generated_at,
        counts: %{running: 0, retrying: 0, queued: 0},
        pending: [],
        error: %{code: "snapshot_unavailable", message: "Snapshot unavailable"}
      }
  end
end
```

- [ ] **Step 2.7: Run tests**

```bash
cd elixir && mix test test/symphony_elixir/presenter_step_info_test.exs --seed 0 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 2.8: Run full test suite**

```bash
cd elixir && mix test --seed 0 2>&1 | tail -20
```

Expected: all tests pass (no regressions).

- [ ] **Step 2.9: Commit**

```bash
cd elixir && git add lib/symphony_elixir_web/presenter.ex test/symphony_elixir/presenter_step_info_test.exs
git commit -m "feat: presenter exposes step_info and pending queue in snapshot payload"
```

---

## Task 3: CSS — new styles for step pips and priority badges

**Files:**
- Modify: `elixir/priv/static/dashboard.css`

- [ ] **Step 3.1: Append new CSS rules**

Open `elixir/priv/static/dashboard.css` and append at the end:

```css
/* Step activity pips */
.step-stack { display: flex; flex-direction: column; gap: 0.3rem; }
.step-header { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
.step-badge {
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 700;
  background: #1d1d1f;
  color: white;
  padding: 0.15rem 0.45rem;
  border-radius: 5px;
  white-space: nowrap;
}
.step-label { font-size: 0.82rem; font-weight: 500; }
.step-pips { display: flex; gap: 3px; flex-wrap: nowrap; }
.pip {
  display: inline-block;
  width: 10px;
  height: 4px;
  border-radius: 2px;
}
.pip-done    { background: #3b82f6; }
.pip-active  { background: #93c5fd; }
.pip-empty   { background: #e5e7eb; }
.step-fraction { font-size: 0.72rem; color: #6e6e73; margin-left: 0.25rem; }

/* Priority badges */
.priority-badge {
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.18rem 0.45rem;
  border-radius: 5px;
}
.priority-urgent { background: #fee2e2; color: #b91c1c; }
.priority-high   { background: #ffedd5; color: #c2410c; }
.priority-medium { background: #fef9c3; color: #854d0e; }
.priority-low    { background: #f0fdf4; color: #166534; }
```

- [ ] **Step 3.2: Commit**

```bash
cd elixir && git add priv/static/dashboard.css
git commit -m "feat: add CSS for step pips and priority badges"
```

---

## Task 4: LiveView — Queued metric card + Pending queue section

**Files:**
- Modify: `elixir/lib/symphony_elixir_web/live/dashboard_live.ex`

- [ ] **Step 4.1: Add helper functions**

Add these private functions to `dashboard_live.ex` (after the existing `state_badge_class/1`):

```elixir
defp pip_class(i, current_step, _total) do
  cond do
    i < current_step -> "pip pip-done"
    i == current_step -> "pip pip-active"
    true -> "pip pip-empty"
  end
end

defp priority_label(1), do: "Urgent"
defp priority_label(2), do: "High"
defp priority_label(3), do: "Medium"
defp priority_label(4), do: "Low"
defp priority_label(_), do: nil

defp priority_badge_class(1), do: "priority-badge priority-urgent"
defp priority_badge_class(2), do: "priority-badge priority-high"
defp priority_badge_class(3), do: "priority-badge priority-medium"
defp priority_badge_class(4), do: "priority-badge priority-low"
defp priority_badge_class(_), do: nil
```

- [ ] **Step 4.2: Add "Queued" metric card**

In the `render/1` function, find the metric grid section:

```heex
<section class="metric-grid">
  <article class="metric-card">
    <p class="metric-label">Running</p>
    ...
  </article>

  <article class="metric-card">
    <p class="metric-label">Retrying</p>
    ...
  </article>
```

Add the new Queued card between "Retrying" and "Total tokens":

```heex
<article class="metric-card">
  <p class="metric-label">Queued</p>
  <p class="metric-value numeric"><%= @payload.counts.queued %></p>
  <p class="metric-detail">Issues waiting for a free slot.</p>
</article>
```

- [ ] **Step 4.3: Add Pending queue section**

After the closing `</section>` of the "Running sessions" section, and before the "Retry queue" `<section>`, add:

```heex
<section class="section-card">
  <div class="section-header">
    <div>
      <h2 class="section-title">Pending queue</h2>
      <p class="section-copy">
        Issues eligible for dispatch, ordered by priority. Waiting for a free slot (max <%= max_concurrent_agents() %> running).
      </p>
    </div>
  </div>

  <%= if @payload.pending == [] do %>
    <p class="empty-state">No issues are queued — all eligible issues are running or no new candidates found.</p>
  <% else %>
    <div class="table-wrap">
      <table class="data-table" style="min-width: 680px;">
        <colgroup>
          <col style="width: 3rem;" />
          <col style="width: 10rem;" />
          <col />
          <col style="width: 7rem;" />
          <col style="width: 6rem;" />
        </colgroup>
        <thead>
          <tr>
            <th>#</th>
            <th>Issue</th>
            <th>Title</th>
            <th>State</th>
            <th>Priority</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={{entry, idx} <- Enum.with_index(@payload.pending, 1)}>
            <td class="numeric muted"><%= idx %></td>
            <td>
              <div class="issue-stack">
                <span class="issue-id"><%= entry.identifier %></span>
                <%= if entry.url do %>
                  <a class="issue-link" href={entry.url} target="_blank" rel="noopener">link</a>
                <% end %>
              </div>
            </td>
            <td>
              <span class="issue-title-text" title={entry.title || ""}>
                <%= truncate_title(entry.title) %>
              </span>
            </td>
            <td>
              <span class={state_badge_class(entry.state)}>
                <%= entry.state %>
              </span>
            </td>
            <td>
              <%= if priority_label(entry.priority) do %>
                <span class={priority_badge_class(entry.priority)}>
                  <%= priority_label(entry.priority) %>
                </span>
              <% end %>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  <% end %>
</section>
```

- [ ] **Step 4.4: Add `truncate_title/1` and `max_concurrent_agents/0` helpers**

Add to the private functions section:

```elixir
defp truncate_title(nil), do: "—"
defp truncate_title(title) when byte_size(title) <= 60, do: title
defp truncate_title(title), do: String.slice(title, 0, 57) <> "…"

defp max_concurrent_agents, do: SymphonyElixir.Config.max_concurrent_agents()
```

- [ ] **Step 4.5: Run mix compile to catch template errors**

```bash
cd elixir && mix compile 2>&1 | grep -E "error|warning" | head -20
```

Expected: no errors.

- [ ] **Step 4.6: Run full test suite**

```bash
cd elixir && mix test --seed 0 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 4.7: Commit**

```bash
cd elixir && git add lib/symphony_elixir_web/live/dashboard_live.ex
git commit -m "feat: add Queued metric card and Pending queue section to dashboard"
```

---

## Task 5: LiveView — Activity column replacing Agent update

**Files:**
- Modify: `elixir/lib/symphony_elixir_web/live/dashboard_live.ex`

- [ ] **Step 5.1: Replace "Agent update" header with "Activity"**

In the running sessions `<thead>`, find:

```heex
<th>Agent update</th>
```

Replace with:

```heex
<th>Activity</th>
```

- [ ] **Step 5.2: Replace the Agent update `<td>` with the Activity cell**

Find the existing agent update cell in the running sessions `<tbody>`:

```heex
<td>
  <div class="detail-stack">
    <span
      class="event-text"
      title={entry.last_message || to_string(entry.last_event || "n/a")}
    ><%= entry.last_message || to_string(entry.last_event || "n/a") %></span>
    <span class="muted event-meta">
      <%= entry.last_event || "n/a" %>
      <%= if entry.last_event_at do %>
        · <span class="mono numeric"><%= entry.last_event_at %></span>
      <% end %>
    </span>
  </div>
</td>
```

Replace with:

```heex
<td>
  <%= case entry.step_info do %>
    <% %{step: step, total: total, label: label} -> %>
      <div class="step-stack">
        <div class="step-header">
          <span class="step-badge">Step <%= step %> / <%= total %></span>
          <span class="step-label"><%= label %></span>
        </div>
        <%= if total <= 15 do %>
          <div class="step-pips">
            <%= for i <- 1..total do %>
              <span class={pip_class(i, step, total)}></span>
            <% end %>
          </div>
        <% else %>
          <span class="step-fraction"><%= step %> / <%= total %></span>
        <% end %>
      </div>
    <% nil -> %>
      <div class="detail-stack">
        <span
          class="event-text"
          title={entry.last_message || to_string(entry.last_event || "n/a")}
        ><%= entry.last_message || to_string(entry.last_event || "n/a") %></span>
        <span class="muted event-meta">
          <%= entry.last_event || "n/a" %>
          <%= if entry.last_event_at do %>
            · <span class="mono numeric"><%= entry.last_event_at %></span>
          <% end %>
        </span>
      </div>
  <% end %>
</td>
```

- [ ] **Step 5.3: Run mix compile**

```bash
cd elixir && mix compile 2>&1 | grep -E "error|warning" | head -20
```

Expected: no errors.

- [ ] **Step 5.4: Run full test suite**

```bash
cd elixir && mix test --seed 0 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5.5: Commit**

```bash
cd elixir && git add lib/symphony_elixir_web/live/dashboard_live.ex
git commit -m "feat: replace Agent update column with Activity step indicator"
```

---

## Task 6: WORKFLOW.md — agent step reporting instruction

**Files:**
- Modify: `elixir/WORKFLOW.md`

- [ ] **Step 6.1: Read the current WORKFLOW.md**

Open `elixir/WORKFLOW.md` and find the section where numbered steps begin (typically after front matter/config).

- [ ] **Step 6.2: Add step reporting instruction**

In the WORKFLOW.md preamble (before or after the step list, in the "instructions to the agent" section), add:

```markdown
## Step reporting

At the start of **each numbered step**, before doing any work for that step, write the following file to the workspace root:

```bash
echo '{"step": N, "total": T, "label": "Step heading text"}' > .symphony-status
```

Where `N` is the current step number (1-based), `T` is the total number of steps, and `"Step heading text"` is the exact heading of that step. Write atomically: write to `.symphony-status.tmp` first, then rename:

```bash
echo '{"step": N, "total": T, "label": "Step heading text"}' > .symphony-status.tmp && mv .symphony-status.tmp .symphony-status
```

This file is read by the Symphony dashboard to show real-time progress. It costs zero tokens and takes one second.
```

- [ ] **Step 6.3: Commit**

```bash
git add elixir/WORKFLOW.md
git commit -m "docs: add .symphony-status step reporting instruction to WORKFLOW.md"
```

---

## Task 7: Test LiveView helper functions

**Files:**
- Create: `elixir/test/symphony_elixir_web/dashboard_live_helpers_test.exs`

The LiveView rendering helpers added in Tasks 4 and 5 are pure functions — they can be tested directly without mounting a live socket.

- [ ] **Step 7.1: Write failing tests**

Create `elixir/test/symphony_elixir_web/dashboard_live_helpers_test.exs`:

```elixir
defmodule SymphonyElixirWeb.DashboardLiveHelpersTest do
  use SymphonyElixir.TestSupport

  # We test the helper functions exported via @doc false
  alias SymphonyElixirWeb.DashboardLive

  describe "pip_class/3" do
    test "returns pip-done for steps before current" do
      assert DashboardLive.pip_class_for_test(1, 3, 5) == "pip pip-done"
      assert DashboardLive.pip_class_for_test(2, 3, 5) == "pip pip-done"
    end

    test "returns pip-active for the current step" do
      assert DashboardLive.pip_class_for_test(3, 3, 5) == "pip pip-active"
    end

    test "returns pip-empty for steps after current" do
      assert DashboardLive.pip_class_for_test(4, 3, 5) == "pip pip-empty"
      assert DashboardLive.pip_class_for_test(5, 3, 5) == "pip pip-empty"
    end

    test "first step: only step 1 is active, rest are empty" do
      assert DashboardLive.pip_class_for_test(1, 1, 4) == "pip pip-active"
      assert DashboardLive.pip_class_for_test(2, 1, 4) == "pip pip-empty"
    end

    test "last step: all previous are done, last is active" do
      assert DashboardLive.pip_class_for_test(3, 4, 4) == "pip pip-done"
      assert DashboardLive.pip_class_for_test(4, 4, 4) == "pip pip-active"
    end
  end

  describe "priority_label/1" do
    test "maps integers 1-4 to correct labels" do
      assert DashboardLive.priority_label_for_test(1) == "Urgent"
      assert DashboardLive.priority_label_for_test(2) == "High"
      assert DashboardLive.priority_label_for_test(3) == "Medium"
      assert DashboardLive.priority_label_for_test(4) == "Low"
    end

    test "returns nil for nil priority" do
      assert DashboardLive.priority_label_for_test(nil) == nil
    end

    test "returns nil for out-of-range integers" do
      assert DashboardLive.priority_label_for_test(0) == nil
      assert DashboardLive.priority_label_for_test(5) == nil
    end
  end

  describe "priority_badge_class/1" do
    test "maps integers 1-4 to correct CSS classes" do
      assert DashboardLive.priority_badge_class_for_test(1) == "priority-badge priority-urgent"
      assert DashboardLive.priority_badge_class_for_test(2) == "priority-badge priority-high"
      assert DashboardLive.priority_badge_class_for_test(3) == "priority-badge priority-medium"
      assert DashboardLive.priority_badge_class_for_test(4) == "priority-badge priority-low"
    end

    test "returns nil for nil and out-of-range" do
      assert DashboardLive.priority_badge_class_for_test(nil) == nil
      assert DashboardLive.priority_badge_class_for_test(0) == nil
    end
  end

  describe "truncate_title/1" do
    test "returns em-dash for nil" do
      assert DashboardLive.truncate_title_for_test(nil) == "—"
    end

    test "returns title unchanged when 60 bytes or fewer" do
      short = String.duplicate("a", 60)
      assert DashboardLive.truncate_title_for_test(short) == short
    end

    test "truncates to 57 chars + ellipsis when over 60 bytes" do
      long = String.duplicate("a", 80)
      result = DashboardLive.truncate_title_for_test(long)
      assert String.ends_with?(result, "…")
      assert byte_size(result) <= 61  # 57 ASCII chars + 3-byte UTF-8 ellipsis
    end

    test "does not truncate a 60-char title" do
      exact = String.duplicate("b", 60)
      assert DashboardLive.truncate_title_for_test(exact) == exact
    end
  end
end
```

- [ ] **Step 7.2: Add test-facing exports to `DashboardLive`**

Add these `@doc false` exports to `elixir/lib/symphony_elixir_web/live/dashboard_live.ex` (after the existing private helper definitions):

```elixir
@doc false
def pip_class_for_test(i, current_step, total), do: pip_class(i, current_step, total)

@doc false
def priority_label_for_test(priority), do: priority_label(priority)

@doc false
def priority_badge_class_for_test(priority), do: priority_badge_class(priority)

@doc false
def truncate_title_for_test(title), do: truncate_title(title)
```

- [ ] **Step 7.3: Run tests to verify they fail first**

```bash
cd elixir && mix test test/symphony_elixir_web/dashboard_live_helpers_test.exs --seed 0 2>&1 | tail -20
```

Expected: compile error — `pip_class_for_test/3` undefined (because Task 4/5 haven't been completed yet if running in isolation, or because the exports don't exist yet).

- [ ] **Step 7.4: Run tests after exports are added**

```bash
cd elixir && mix test test/symphony_elixir_web/dashboard_live_helpers_test.exs --seed 0 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 7.5: Commit**

```bash
cd elixir && git add test/symphony_elixir_web/dashboard_live_helpers_test.exs lib/symphony_elixir_web/live/dashboard_live.ex
git commit -m "test: add unit tests for DashboardLive helper functions"
```

---

## Task 9: Final verification

- [ ] **Step 7.1: Run full test suite**

```bash
cd elixir && mix test --seed 0 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 7.2: Run mix format check**

```bash
cd elixir && mix format --check-formatted 2>&1
```

If any files are flagged, run `mix format` and commit:

```bash
cd elixir && mix format && git add -p && git commit -m "style: mix format"
```

- [ ] **Step 7.3: Smoke test the dashboard manually**

Start Symphony and open the dashboard URL. Verify:
1. "Queued" metric card appears (shows 0 when no pending issues)
2. Running sessions table shows "Activity" column header
3. If a `.symphony-status` file exists in a workspace, the step badge and pips render
4. If no `.symphony-status` file exists, the fallback (last event text) renders
5. Pending queue section renders with correct ordering when issues are queued

- [ ] **Step 7.4: Final commit if anything was fixed**

```bash
git log --oneline -6
```

Expected output (7 commits from this feature):
```
<sha> docs: add .symphony-status step reporting instruction to WORKFLOW.md
<sha> test: add unit tests for DashboardLive helper functions
<sha> feat: replace Agent update column with Activity step indicator
<sha> feat: add Queued metric card and Pending queue section to dashboard
<sha> feat: add CSS for step pips and priority badges
<sha> feat: presenter exposes step_info and pending queue in snapshot payload
<sha> feat: orchestrator tracks pending queue after each poll cycle
```
