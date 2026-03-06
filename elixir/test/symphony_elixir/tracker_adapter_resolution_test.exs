defmodule SymphonyElixir.TrackerAdapterResolutionTest do
  use SymphonyElixir.TestSupport

  describe "Tracker.adapter/0" do
    test "resolves to Memory adapter when kind is memory" do
      write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "memory")
      assert Tracker.adapter() == SymphonyElixir.Tracker.Memory
    end

    test "resolves to GitHub adapter when kind is github" do
      write_workflow_file!(Workflow.workflow_file_path(),
        tracker_kind: "github",
        tracker_repo: "owner/repo",
        tracker_label_prefix: "sym"
      )

      assert Tracker.adapter() == SymphonyElixir.GitHub.Adapter
    end

    test "defaults to Linear adapter for unrecognized kind" do
      write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "linear")
      assert Tracker.adapter() == SymphonyElixir.Linear.Adapter
    end

    test "defaults to Linear adapter when kind is unset" do
      write_workflow_file!(Workflow.workflow_file_path(), tracker_kind: "unknown")
      assert Tracker.adapter() == SymphonyElixir.Linear.Adapter
    end
  end

  describe "CodingAgent.adapter/0" do
    test "resolves to Claude adapter when kind is claude" do
      write_workflow_file!(Workflow.workflow_file_path(), agent_kind: "claude")
      assert SymphonyElixir.CodingAgent.adapter() == SymphonyElixir.Claude.AppServer
    end

    test "defaults to Codex adapter for unrecognized kind" do
      write_workflow_file!(Workflow.workflow_file_path(), agent_kind: "codex")
      assert SymphonyElixir.CodingAgent.adapter() == SymphonyElixir.Codex.AppServer
    end

    test "defaults to Codex adapter when kind is unset" do
      write_workflow_file!(Workflow.workflow_file_path(), agent_kind: nil)
      assert SymphonyElixir.CodingAgent.adapter() == SymphonyElixir.Codex.AppServer
    end
  end
end
