defmodule SymphonyElixir.Jira.ClientTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.Jira.Client
  alias SymphonyElixir.Workflow

  setup do
    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "jira",
      tracker_base_url: "https://example.atlassian.net",
      tracker_email: "agent@example.com",
      tracker_api_token: "test-jira-token",
      tracker_project_keys: ["TEST", "FOO"],
      tracker_trigger_label: "agent:todo"
    )

    :ok
  end

  describe "fetch_candidate_issues/1" do
    test "queries Jira search/jql with JQL filtering by project_keys and trigger_label, returns normalized issues" do
      request_fun = fn %{method: :post, url: url, auth: auth, body: body} ->
        assert url == "https://example.atlassian.net/rest/api/3/search/jql"
        assert auth == {:basic, "agent@example.com", "test-jira-token"}
        assert body["jql"] =~ ~s/project IN ("TEST", "FOO")/
        assert body["jql"] =~ ~s/labels = "agent:todo"/
        refute Map.has_key?(body, "nextPageToken")
        assert is_list(body["fields"])

        {:ok,
         %{
           status: 200,
           body: %{
             "total" => 1,
             "isLast" => true,
             "issues" => [
               %{
                 "key" => "TEST-42",
                 "fields" => %{
                   "summary" => "Fix the bug",
                   "description" => "Something is broken",
                   "status" => %{"name" => "To Do"},
                   "labels" => ["agent:todo", "Backend"],
                   "priority" => %{"name" => "High"},
                   "assignee" => %{"accountId" => "abc123"},
                   "created" => "2026-01-01T10:00:00.000+0000",
                   "updated" => "2026-01-02T10:00:00.000+0000"
                 }
               }
             ]
           }
         }}
      end

      assert {:ok, [issue]} = Client.fetch_candidate_issues(request_fun: request_fun)
      assert issue.id == "TEST-42"
      assert issue.identifier == "TEST-42"
      assert issue.title == "Fix the bug"
      assert issue.description == "Something is broken"
      assert issue.state == "To Do"
      assert issue.priority == 2
      assert issue.assignee_id == "abc123"
      assert issue.labels == ["agent:todo", "backend"]
      assert issue.url == "https://example.atlassian.net/browse/TEST-42"
    end

    test "handles ADF description by flattening text nodes" do
      request_fun = fn %{method: :post} ->
        {:ok,
         %{
           status: 200,
           body: %{
             "total" => 1,
             "isLast" => true,
             "issues" => [
               %{
                 "key" => "TEST-1",
                 "fields" => %{
                   "summary" => "Has ADF",
                   "description" => %{
                     "type" => "doc",
                     "content" => [
                       %{"type" => "paragraph", "content" => [%{"type" => "text", "text" => "Hello"}]},
                       %{"type" => "paragraph", "content" => [%{"type" => "text", "text" => "World"}]}
                     ]
                   },
                   "status" => %{"name" => "To Do"},
                   "labels" => [],
                   "priority" => nil,
                   "assignee" => nil
                 }
               }
             ]
           }
         }}
      end

      assert {:ok, [issue]} = Client.fetch_candidate_issues(request_fun: request_fun)
      assert issue.description == "Hello\nWorld"
    end

    # We only test the `"` escape here, not backslash. The escape_jql/1
    # implementation handles both (backslash first, then `"`), but our test
    # fixture writer in test_support.exs:yaml_value/1 only escapes `"` when
    # serializing the trigger_label through the YAML frontmatter — backslash
    # would not survive that round-trip, so a backslash test would be testing
    # the fixture writer, not escape_jql. `"` is the higher-risk character in
    # JQL anyway (it can break out of the string literal); backslash matters
    # only for defense-in-depth.
    test "escapes double-quotes in trigger_label so a malicious or weird label cannot break the JQL" do
      write_workflow_file!(Workflow.workflow_file_path(),
        tracker_kind: "jira",
        tracker_base_url: "https://example.atlassian.net",
        tracker_email: "agent@example.com",
        tracker_api_token: "test-jira-token",
        tracker_project_keys: ["TEST"],
        tracker_trigger_label: ~S(agent:"weird":todo)
      )

      request_fun = fn %{method: :post, body: body} ->
        # The inner double-quotes are escaped as \" inside the JQL string literal,
        # preserving the JQL syntax: labels = "agent:\"weird\":todo"
        assert body["jql"] =~ ~S(labels = "agent:\"weird\":todo")
        {:ok, %{status: 200, body: %{"issues" => [], "isLast" => true}}}
      end

      assert {:ok, []} = Client.fetch_candidate_issues(request_fun: request_fun)
    end

    test "omits project clause when project_keys is empty (cross-project mode)" do
      write_workflow_file!(Workflow.workflow_file_path(),
        tracker_kind: "jira",
        tracker_base_url: "https://example.atlassian.net",
        tracker_email: "agent@example.com",
        tracker_api_token: "test-jira-token",
        tracker_project_keys: [],
        tracker_trigger_label: "agent:todo"
      )

      request_fun = fn %{method: :post, body: body} ->
        refute body["jql"] =~ "project IN"
        assert body["jql"] =~ ~s/labels = "agent:todo"/
        {:ok, %{status: 200, body: %{"issues" => [], "isLast" => true}}}
      end

      assert {:ok, []} = Client.fetch_candidate_issues(request_fun: request_fun)
    end

    test "follows nextPageToken pagination until isLast: true" do
      {:ok, agent} = Agent.start_link(fn -> 0 end)

      request_fun = fn %{method: :post, body: body} ->
        call_index = Agent.get_and_update(agent, fn n -> {n, n + 1} end)

        case call_index do
          0 ->
            refute Map.has_key?(body, "nextPageToken")

            {:ok,
             %{
               status: 200,
               body: %{
                 "issues" => [issue_fixture("TEST-1")],
                 "isLast" => false,
                 "nextPageToken" => "page-token-2"
               }
             }}

          1 ->
            assert body["nextPageToken"] == "page-token-2"

            {:ok,
             %{
               status: 200,
               body: %{
                 "issues" => [issue_fixture("TEST-2")],
                 "isLast" => true
               }
             }}
        end
      end

      assert {:ok, [%{id: "TEST-1"}, %{id: "TEST-2"}]} =
               Client.fetch_candidate_issues(request_fun: request_fun)

      Agent.stop(agent)
    end

    test "returns error on 401" do
      request_fun = fn _ -> {:ok, %{status: 401, body: "Unauthorized"}} end
      assert {:error, {:jira_api_status, 401}} = Client.fetch_candidate_issues(request_fun: request_fun)
    end

    defp issue_fixture(key) do
      %{
        "key" => key,
        "fields" => %{
          "summary" => "Issue #{key}",
          "description" => nil,
          "status" => %{"name" => "To Do"},
          "labels" => [],
          "priority" => nil,
          "assignee" => nil
        }
      }
    end

    test "returns error when API token missing" do
      System.delete_env("JIRA_TOKEN")

      write_workflow_file!(Workflow.workflow_file_path(),
        tracker_kind: "jira",
        tracker_base_url: "https://example.atlassian.net",
        tracker_email: "agent@example.com",
        tracker_api_token: nil,
        tracker_project_keys: ["TEST"]
      )

      assert {:error, :missing_jira_api_token} =
               Client.fetch_candidate_issues(request_fun: fn _ -> :unreachable end)
    end
  end

  describe "fetch_issues_by_states/2" do
    test "builds JQL using status IN (...) with the configured project_keys" do
      request_fun = fn %{method: :post, body: body} ->
        assert body["jql"] =~ ~s/project IN ("TEST", "FOO")/
        assert body["jql"] =~ ~s/status IN ("In Progress", "Done")/
        {:ok, %{status: 200, body: %{"issues" => [], "isLast" => true}}}
      end

      assert {:ok, []} =
               Client.fetch_issues_by_states(["In Progress", "Done"], request_fun: request_fun)
    end

    test "short-circuits on empty state list without calling the API" do
      request_fun = fn _ -> raise "should not be called" end
      assert {:ok, []} = Client.fetch_issues_by_states([], request_fun: request_fun)
    end
  end

  describe "fetch_issue_states_by_ids/2" do
    test "builds JQL using key IN (...)" do
      request_fun = fn %{method: :post, body: body} ->
        assert body["jql"] =~ ~s/key IN ("TEST-1", "TEST-2")/
        {:ok, %{status: 200, body: %{"issues" => [], "isLast" => true}}}
      end

      assert {:ok, []} = Client.fetch_issue_states_by_ids(["TEST-1", "TEST-2"], request_fun: request_fun)
    end
  end

  describe "create_comment/3" do
    test "POSTs the comment body to the v2 endpoint with Basic auth" do
      request_fun = fn %{method: :post, url: url, auth: auth, body: body} ->
        assert url == "https://example.atlassian.net/rest/api/2/issue/TEST-42/comment"
        assert auth == {:basic, "agent@example.com", "test-jira-token"}
        assert body == %{"body" => "Hello from agent"}
        {:ok, %{status: 201, body: %{}}}
      end

      assert :ok = Client.create_comment("TEST-42", "Hello from agent", request_fun: request_fun)
    end

    test "returns error on non-2xx" do
      request_fun = fn _ -> {:ok, %{status: 400, body: "{\"errorMessages\": [\"bad request\"]}"}} end
      assert {:error, {:jira_api_status, 400}} = Client.create_comment("TEST-42", "x", request_fun: request_fun)
    end
  end

  describe "upload_attachment/3" do
    test "POSTs multipart to v3 attachments URL with Basic auth and X-Atlassian-Token header, returns confirmed filename" do
      request_fun = fn %{method: :post_multipart, url: url, auth: auth, file_path: _file_path, mime_type: mime_type} ->
        assert url == "https://example.atlassian.net/rest/api/3/issue/TEST-42/attachments"
        assert auth == {:basic, "agent@example.com", "test-jira-token"}
        assert mime_type == "image/png"
        # The actual headers are built by default_request_fun, not passed through request map.
        # We verify the shape is correct and returns the confirmed filename from the response.
        {:ok, %{status: 200, body: [%{"filename" => "banner (1).png", "id" => "att-1"}]}}
      end

      assert {:ok, "banner (1).png"} =
               Client.upload_attachment("TEST-42", "/tmp/banner.png", "image/png", request_fun: request_fun)
    end

    test "returns confirmed filename from response (not input filename) when Jira renames duplicate" do
      request_fun = fn %{method: :post_multipart} ->
        {:ok, %{status: 201, body: [%{"filename" => "banner (1).png"}]}}
      end

      assert {:ok, "banner (1).png"} =
               Client.upload_attachment("TEST-42", "/tmp/banner.png", "image/png", request_fun: request_fun)
    end

    test "returns error on non-2xx status" do
      request_fun = fn _ -> {:ok, %{status: 400, body: %{"errorMessages" => ["bad request"]}}} end

      assert {:error, {:jira_api_status, 400}} =
               Client.upload_attachment("TEST-42", "/tmp/banner.png", "image/png", request_fun: request_fun)
    end

    test "returns :empty_attachment_response when response is an empty list" do
      request_fun = fn _ -> {:ok, %{status: 200, body: []}} end

      assert {:error, :empty_attachment_response} =
               Client.upload_attachment("TEST-42", "/tmp/banner.png", "image/png", request_fun: request_fun)
    end
  end

  describe "update_issue_state/3" do
    test "lists transitions then POSTs the matching transition id (matches by 'to.name')" do
      test_pid = self()

      request_fun = fn
        %{method: :get, url: url} ->
          assert url == "https://example.atlassian.net/rest/api/3/issue/TEST-42/transitions"
          send(test_pid, :listed_transitions)

          {:ok,
           %{
             status: 200,
             body: %{
               "transitions" => [
                 %{"id" => "11", "name" => "Start Progress", "to" => %{"name" => "In Progress"}},
                 %{"id" => "21", "name" => "Move to Review", "to" => %{"name" => "In Review"}},
                 %{"id" => "31", "name" => "Done", "to" => %{"name" => "Done"}}
               ]
             }
           }}

        %{method: :post, url: url, body: body} ->
          assert url == "https://example.atlassian.net/rest/api/3/issue/TEST-42/transitions"
          assert body == %{"transition" => %{"id" => "21"}}
          send(test_pid, :executed_transition)
          {:ok, %{status: 204, body: ""}}
      end

      assert :ok = Client.update_issue_state("TEST-42", "In Review", request_fun: request_fun)
      assert_received :listed_transitions
      assert_received :executed_transition
    end

    test "returns transition_not_found when state has no matching transition" do
      request_fun = fn
        %{method: :get} ->
          {:ok,
           %{
             status: 200,
             body: %{
               "transitions" => [
                 %{"id" => "11", "name" => "Start Progress", "to" => %{"name" => "In Progress"}}
               ]
             }
           }}

        %{method: :post} ->
          raise "transition should not be executed when state not found"
      end

      assert {:error, {:jira_transition_not_found, "Closed"}} =
               Client.update_issue_state("TEST-42", "Closed", request_fun: request_fun)
    end
  end
end
