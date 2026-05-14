defmodule SymphonyElixir.Jira.Client do
  @moduledoc """
  Jira REST API client for issue tracking. Uses Jira Cloud REST API v3 for searches
  and reads, and v2 for comment writes (v2 accepts plain wiki markup strings, v3 requires ADF).
  """

  require Logger
  alias SymphonyElixir.{Issue, Jira}

  @search_page_size 50
  @max_error_body_log_bytes 1_000

  @spec fetch_candidate_issues(keyword()) :: {:ok, [Issue.t()]} | {:error, term()}
  def fetch_candidate_issues(opts \\ []) do
    with {:ok, auth} <- build_auth() do
      jql = candidate_jql(Jira.Config.project_keys(), Jira.Config.trigger_label())
      do_search(jql, auth, opts)
    end
  end

  @spec fetch_issues_by_states([String.t()], keyword()) :: {:ok, [Issue.t()]} | {:error, term()}
  def fetch_issues_by_states(state_names, opts \\ []) when is_list(state_names) do
    case Enum.map(state_names, &to_string/1) |> Enum.uniq() do
      [] ->
        {:ok, []}

      states ->
        with {:ok, auth} <- build_auth() do
          jql = states_jql(Jira.Config.project_keys(), states)
          do_search(jql, auth, opts)
        end
    end
  end

  @spec fetch_issue_states_by_ids([String.t()], keyword()) :: {:ok, [Issue.t()]} | {:error, term()}
  def fetch_issue_states_by_ids(issue_ids, opts \\ []) when is_list(issue_ids) do
    case Enum.uniq(issue_ids) do
      [] ->
        {:ok, []}

      keys ->
        with {:ok, auth} <- build_auth() do
          jql = keys_jql(keys)
          do_search(jql, auth, opts)
        end
    end
  end

  @spec create_comment(String.t(), String.t(), keyword()) :: :ok | {:error, term()}
  def create_comment(issue_key, body, opts \\ []) when is_binary(issue_key) and is_binary(body) do
    with {:ok, auth} <- build_auth(),
         {:ok, base_url} <- require_base_url() do
      request_fun = Keyword.get(opts, :request_fun, &default_request_fun/1)
      url = "#{base_url}/rest/api/2/issue/#{issue_key}/comment"

      case request_fun.(%{method: :post, url: url, auth: auth, body: %{"body" => body}}) do
        {:ok, %{status: status}} when status in [200, 201] ->
          :ok

        {:ok, %{status: status} = response} ->
          Logger.error("Jira create_comment failed status=#{status} #{jira_error_context(response)}")
          {:error, {:jira_api_status, status}}

        {:error, reason} ->
          Logger.error("Jira create_comment request failed: #{inspect(reason)}")
          {:error, {:jira_api_request, reason}}
      end
    end
  end

  @spec update_issue_state(String.t(), String.t(), keyword()) :: :ok | {:error, term()}
  def update_issue_state(issue_key, state_name, opts \\ [])
      when is_binary(issue_key) and is_binary(state_name) do
    with {:ok, auth} <- build_auth(),
         {:ok, base_url} <- require_base_url() do
      request_fun = Keyword.get(opts, :request_fun, &default_request_fun/1)

      with {:ok, transition_id} <- resolve_transition_id(issue_key, state_name, request_fun, auth, base_url) do
        execute_transition(issue_key, transition_id, request_fun, auth, base_url)
      end
    end
  end

  # -- Private helpers --------------------------------------------------------

  defp candidate_jql(project_keys, trigger_label) do
    label_clause = ~s/labels = "#{escape_jql(trigger_label)}"/
    jql_with_optional_project(project_keys, label_clause) <> " ORDER BY created ASC"
  end

  defp states_jql(project_keys, state_names) do
    quoted = Enum.map_join(state_names, ", ", &~s/"#{escape_jql(&1)}"/)
    jql_with_optional_project(project_keys, "status IN (#{quoted})") <> " ORDER BY created ASC"
  end

  defp keys_jql(keys) do
    quoted = Enum.map_join(keys, ", ", &~s/"#{escape_jql(&1)}"/)
    "key IN (#{quoted})"
  end

  # When project_keys is configured, narrow with `project IN (...) AND <clause>`.
  # When empty, return just `<clause>` so the JQL is genuinely cross-project.
  defp jql_with_optional_project(project_keys, clause) when is_list(project_keys) and project_keys != [] do
    quoted = Enum.map_join(project_keys, ", ", &~s/"#{escape_jql(&1)}"/)
    "project IN (#{quoted}) AND #{clause}"
  end

  defp jql_with_optional_project(_project_keys, clause), do: clause

  defp escape_jql(value) when is_binary(value) do
    value
    |> String.replace("\\", "\\\\")
    |> String.replace("\"", "\\\"")
  end

  defp do_search(jql, auth, opts) do
    with {:ok, base_url} <- require_base_url() do
      request_fun = Keyword.get(opts, :request_fun, &default_request_fun/1)
      do_search_page(jql, auth, request_fun, base_url, nil, [])
    end
  end

  # Jira deprecated /rest/api/3/search (removed in 2025); the replacement
  # /rest/api/3/search/jql uses token-based pagination (nextPageToken) instead
  # of startAt + total. See CHANGE-2046.
  defp do_search_page(jql, auth, request_fun, base_url, next_page_token, acc) do
    url = "#{base_url}/rest/api/3/search/jql"

    payload =
      %{
        "jql" => jql,
        "maxResults" => @search_page_size,
        "fields" => issue_fields()
      }
      |> maybe_put_token(next_page_token)

    case request_fun.(%{method: :post, url: url, auth: auth, body: payload}) do
      {:ok, %{status: 200, body: body}} when is_map(body) ->
        issues = decode_issues(body)
        acc = acc ++ issues

        case next_token(body) do
          nil -> {:ok, acc}
          token -> do_search_page(jql, auth, request_fun, base_url, token, acc)
        end

      {:ok, %{status: status} = response} ->
        Logger.error("Jira search failed status=#{status} #{jira_error_context(response)}")
        {:error, {:jira_api_status, status}}

      {:error, reason} ->
        Logger.error("Jira search request failed: #{inspect(reason)}")
        {:error, {:jira_api_request, reason}}
    end
  end

  defp maybe_put_token(payload, nil), do: payload
  defp maybe_put_token(payload, token) when is_binary(token), do: Map.put(payload, "nextPageToken", token)

  defp next_token(body) do
    cond do
      Map.get(body, "isLast") == true -> nil
      is_binary(Map.get(body, "nextPageToken")) -> Map.get(body, "nextPageToken")
      true -> nil
    end
  end

  defp issue_fields, do: ["summary", "description", "status", "labels", "priority", "assignee", "comment", "created", "updated"]

  defp decode_issues(%{"issues" => issues}) when is_list(issues), do: Enum.map(issues, &normalize_issue/1)
  defp decode_issues(_), do: []

  defp normalize_issue(jira_issue) when is_map(jira_issue) do
    fields = Map.get(jira_issue, "fields", %{})
    key = Map.get(jira_issue, "key")

    if is_nil(key) do
      Logger.warning("Jira issue missing `key` field — will be dropped by orchestrator filter. Raw: #{inspect(jira_issue, limit: 5)}")
    end

    %Issue{
      id: key,
      identifier: key,
      title: Map.get(fields, "summary"),
      description: extract_description(Map.get(fields, "description")),
      priority: extract_priority(Map.get(fields, "priority")),
      state: get_in(fields, ["status", "name"]),
      branch_name: nil,
      url: build_browse_url(key),
      assignee_id: get_in(fields, ["assignee", "accountId"]),
      labels: extract_labels(Map.get(fields, "labels", [])),
      assigned_to_worker: true,
      created_at: parse_datetime(Map.get(fields, "created")),
      updated_at: parse_datetime(Map.get(fields, "updated"))
    }
  end

  defp normalize_issue(_), do: nil

  defp extract_description(nil), do: nil
  defp extract_description(value) when is_binary(value), do: value

  # ADF (Atlassian Document Format) — flatten text nodes for prompt context.
  defp extract_description(%{"content" => content}) when is_list(content) do
    content |> Enum.map(&flatten_adf_node/1) |> Enum.join("\n") |> normalize_or_nil()
  end

  defp extract_description(_), do: nil

  defp flatten_adf_node(%{"text" => text}) when is_binary(text), do: text

  defp flatten_adf_node(%{"content" => children}) when is_list(children),
    do: children |> Enum.map(&flatten_adf_node/1) |> Enum.join("")

  defp flatten_adf_node(_), do: ""

  defp normalize_or_nil(""), do: nil
  defp normalize_or_nil(value), do: value

  defp extract_priority(%{"name" => name}) when is_binary(name) do
    case String.downcase(name) do
      "highest" -> 1
      "high" -> 2
      "medium" -> 3
      "low" -> 4
      "lowest" -> 5
      _ -> nil
    end
  end

  defp extract_priority(_), do: nil

  defp extract_labels(labels) when is_list(labels) do
    labels
    |> Enum.filter(&is_binary/1)
    |> Enum.map(&String.downcase/1)
  end

  defp extract_labels(_), do: []

  defp build_browse_url(nil), do: nil

  defp build_browse_url(key) when is_binary(key) do
    case Jira.Config.base_url() do
      nil -> nil
      base -> String.trim_trailing(base, "/") <> "/browse/" <> key
    end
  end

  defp parse_datetime(nil), do: nil

  defp parse_datetime(raw) when is_binary(raw) do
    case DateTime.from_iso8601(raw) do
      {:ok, dt, _offset} -> dt
      _ -> nil
    end
  end

  defp resolve_transition_id(issue_key, state_name, request_fun, auth, base_url) do
    url = "#{base_url}/rest/api/3/issue/#{issue_key}/transitions"

    case request_fun.(%{method: :get, url: url, auth: auth}) do
      {:ok, %{status: 200, body: %{"transitions" => transitions}}} when is_list(transitions) ->
        case Enum.find(transitions, fn t -> matches_state?(t, state_name) end) do
          %{"id" => id, "name" => name} = match ->
            to_name = get_in(match, ["to", "name"])

            Logger.info("Jira transition selected issue=#{issue_key} target_state=#{inspect(state_name)} transition_id=#{id} transition_name=#{inspect(name)} to=#{inspect(to_name)}")

            {:ok, id}

          _ ->
            available = Enum.map(transitions, &Map.take(&1, ["name", "to"]))

            Logger.warning("Jira transition not found issue=#{issue_key} target_state=#{inspect(state_name)} available=#{inspect(available)}")

            {:error, {:jira_transition_not_found, state_name}}
        end

      {:ok, %{status: status} = response} ->
        Logger.error("Jira list transitions failed status=#{status} #{jira_error_context(response)}")
        {:error, {:jira_api_status, status}}

      {:error, reason} ->
        {:error, {:jira_api_request, reason}}
    end
  end

  defp matches_state?(%{"name" => name, "to" => %{"name" => to_name}}, state_name) do
    String.downcase(to_string(name)) == String.downcase(state_name) or
      String.downcase(to_string(to_name)) == String.downcase(state_name)
  end

  defp matches_state?(%{"name" => name}, state_name) do
    String.downcase(to_string(name)) == String.downcase(state_name)
  end

  defp matches_state?(_, _), do: false

  defp execute_transition(issue_key, transition_id, request_fun, auth, base_url) do
    url = "#{base_url}/rest/api/3/issue/#{issue_key}/transitions"
    body = %{"transition" => %{"id" => transition_id}}

    case request_fun.(%{method: :post, url: url, auth: auth, body: body}) do
      {:ok, %{status: status}} when status in [200, 204] ->
        :ok

      {:ok, %{status: status} = response} ->
        Logger.error("Jira transition failed status=#{status} #{jira_error_context(response)}")
        {:error, {:jira_api_status, status}}

      {:error, reason} ->
        {:error, {:jira_api_request, reason}}
    end
  end

  defp build_auth do
    email = Jira.Config.email()
    token = Jira.Config.api_token()

    cond do
      is_nil(email) -> {:error, :missing_jira_email}
      is_nil(token) -> {:error, :missing_jira_api_token}
      true -> {:ok, {:basic, email, token}}
    end
  end

  defp require_base_url do
    case Jira.Config.base_url() do
      nil -> {:error, :missing_jira_base_url}
      url -> {:ok, String.trim_trailing(url, "/")}
    end
  end

  defp default_request_fun(%{method: :get, url: url, auth: auth}) do
    Req.get(url, headers: jira_headers(auth), connect_options: [timeout: 30_000])
  end

  defp default_request_fun(%{method: :post, url: url, auth: auth, body: body}) do
    Req.post(url, headers: jira_headers(auth), json: body, connect_options: [timeout: 30_000])
  end

  defp jira_headers({:basic, email, token}) do
    encoded = Base.encode64("#{email}:#{token}")

    [
      {"Authorization", "Basic #{encoded}"},
      {"Accept", "application/json"},
      {"Content-Type", "application/json"}
    ]
  end

  defp jira_error_context(%{body: body}) do
    body
    |> summarize_error_body()
    |> then(&("body=" <> &1))
  end

  defp jira_error_context(_), do: ""

  defp summarize_error_body(body) when is_binary(body) do
    body
    |> String.replace(~r/\s+/, " ")
    |> String.trim()
    |> truncate_error_body()
    |> inspect()
  end

  defp summarize_error_body(body) do
    body
    |> inspect(limit: 20, printable_limit: @max_error_body_log_bytes)
    |> truncate_error_body()
  end

  defp truncate_error_body(body) when is_binary(body) do
    if byte_size(body) > @max_error_body_log_bytes do
      binary_part(body, 0, @max_error_body_log_bytes) <> "...<truncated>"
    else
      body
    end
  end
end
