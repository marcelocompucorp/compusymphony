defmodule SymphonyElixir.Jira.Tracker do
  @moduledoc """
  Jira-backed tracker implementation.
  """

  @behaviour SymphonyElixir.Tracker

  alias SymphonyElixir.Jira.Client
  alias SymphonyElixir.Jira.Config

  @spec project_identity() :: String.t() | nil
  def project_identity do
    case Config.project_keys() do
      [] -> nil
      [key] -> key
      keys -> Enum.join(keys, ",")
    end
  end

  @spec default_prompt_template() :: String.t()
  def default_prompt_template do
    """
    You are working on a Jira issue.

    Key: {{ issue.identifier }}
    Title: {{ issue.title }}

    Body:
    {% if issue.description %}
    {{ issue.description }}
    {% else %}
    No description provided.
    {% endif %}
    """
  end

  @spec fetch_candidate_issues() :: {:ok, [term()]} | {:error, term()}
  def fetch_candidate_issues, do: client_module().fetch_candidate_issues()

  @spec fetch_issues_by_states([String.t()]) :: {:ok, [term()]} | {:error, term()}
  def fetch_issues_by_states(states), do: client_module().fetch_issues_by_states(states)

  @spec fetch_issue_states_by_ids([String.t()]) :: {:ok, [term()]} | {:error, term()}
  def fetch_issue_states_by_ids(issue_ids), do: client_module().fetch_issue_states_by_ids(issue_ids)

  @spec create_comment(String.t(), String.t()) :: :ok | {:error, term()}
  def create_comment(issue_id, body) when is_binary(issue_id) and is_binary(body) do
    client_module().create_comment(issue_id, body)
  end

  @spec update_issue_state(String.t(), String.t()) :: :ok | {:error, term()}
  def update_issue_state(issue_id, state_name)
      when is_binary(issue_id) and is_binary(state_name) do
    client_module().update_issue_state(issue_id, state_name)
  end

  @spec upload_attachment(String.t(), String.t(), String.t()) :: {:ok, String.t()} | {:error, term()}
  def upload_attachment(issue_id, file_path, mime_type \\ "image/png") do
    client_module().upload_attachment(issue_id, file_path, mime_type)
  end

  defp client_module do
    Application.get_env(:symphony_elixir, :jira_client_module, Client)
  end
end
