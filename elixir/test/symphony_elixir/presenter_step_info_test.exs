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
