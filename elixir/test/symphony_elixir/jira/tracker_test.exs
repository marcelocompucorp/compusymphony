defmodule SymphonyElixir.Jira.TrackerTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.Jira.Tracker, as: JiraTracker
  alias SymphonyElixir.Workflow

  defmodule MockJiraClient do
    alias SymphonyElixir.Issue

    def fetch_candidate_issues do
      {:ok, [%Issue{id: "TEST-1", identifier: "TEST-1", title: "Sample bug"}]}
    end

    def fetch_issues_by_states(states),
      do: {:ok, Enum.map(states, fn _ -> %Issue{id: "TEST-1"} end)}

    def fetch_issue_states_by_ids(ids), do: {:ok, Enum.map(ids, fn id -> %Issue{id: id} end)}
    def create_comment(_id, _body), do: :ok
    def update_issue_state(_id, _state), do: :ok
  end

  setup do
    Application.put_env(:symphony_elixir, :jira_client_module, MockJiraClient)

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "jira",
      tracker_base_url: "https://example.atlassian.net",
      tracker_email: "test@example.com",
      tracker_api_token: "test-token",
      tracker_project_keys: ["TEST"]
    )

    on_exit(fn ->
      Application.delete_env(:symphony_elixir, :jira_client_module)
    end)

    :ok
  end

  test "implements Tracker behaviour: fetch_candidate_issues" do
    assert {:ok, [issue]} = JiraTracker.fetch_candidate_issues()
    assert issue.id == "TEST-1"
  end

  test "fetch_issues_by_states delegates to client" do
    assert {:ok, issues} = JiraTracker.fetch_issues_by_states(["In Progress"])
    assert length(issues) == 1
  end

  test "fetch_issue_states_by_ids delegates to client" do
    assert {:ok, [issue]} = JiraTracker.fetch_issue_states_by_ids(["TEST-42"])
    assert issue.id == "TEST-42"
  end

  test "create_comment delegates to client" do
    assert :ok = JiraTracker.create_comment("TEST-42", "comment body")
  end

  test "update_issue_state delegates to client" do
    assert :ok = JiraTracker.update_issue_state("TEST-42", "In Review")
  end

  test "default_prompt_template references issue.identifier" do
    template = JiraTracker.default_prompt_template()
    assert template =~ "{{ issue.identifier }}"
    assert template =~ "{{ issue.title }}"
  end

  test "project_identity returns the single project key when one is configured" do
    assert JiraTracker.project_identity() == "TEST"
  end

  test "tracker dispatcher routes to Jira adapter when kind is jira" do
    assert Tracker.adapter() == SymphonyElixir.Jira.Tracker
  end
end
