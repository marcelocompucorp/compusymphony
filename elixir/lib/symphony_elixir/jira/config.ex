defmodule SymphonyElixir.Jira.Config do
  @moduledoc """
  Jira-specific configuration read from the `jira:` YAML section.
  """

  require Logger

  @behaviour SymphonyElixir.TrackerConfig

  @spec base_url() :: String.t() | nil
  def base_url do
    section_value("base_url")
    |> resolve_env_value(System.get_env("JIRA_URL"))
    |> normalize_string()
  end

  @spec email() :: String.t() | nil
  def email do
    section_value("email")
    |> resolve_env_value(System.get_env("JIRA_USER"))
    |> normalize_string()
  end

  @spec api_token() :: String.t() | nil
  def api_token do
    section_value("api_token")
    |> resolve_env_value(System.get_env("JIRA_TOKEN"))
    |> normalize_string()
  end

  @spec project_keys() :: [String.t()]
  def project_keys do
    case section_value("project_keys") do
      nil ->
        []

      values when is_list(values) ->
        values
        |> Enum.map(&normalize_string/1)
        |> Enum.reject(&is_nil/1)

      value when is_binary(value) ->
        value
        |> String.split(",")
        |> Enum.map(&normalize_string/1)
        |> Enum.reject(&is_nil/1)

      other ->
        # Bad config (e.g. `project_keys: 42`) would otherwise fall through to
        # `[]` silently — which means cross-project mode kicks in without the
        # operator noticing. Log loudly. We still return `[]` to keep the call
        # site simple; `validate!/0` does not fail because the cross-project
        # mode is a valid configuration (no project_keys at all). Operators
        # should treat this warning as a config error.
        Logger.warning(
          "Jira config: jira.project_keys is set but is not a list or comma-separated string " <>
            "(got #{inspect(other)}). Treating as empty — this enables cross-project mode. " <>
            "Fix the config or remove the key to silence this warning."
        )

        []
    end
  end

  @spec trigger_label() :: String.t()
  def trigger_label do
    normalize_string(section_value("trigger_label")) || "agent:todo"
  end

  @impl SymphonyElixir.TrackerConfig
  def validate! do
    cond do
      !is_binary(base_url()) ->
        {:error, "Jira base URL missing — set jira.base_url in WORKFLOW.md or JIRA_URL env var"}

      !is_binary(email()) ->
        {:error, "Jira email missing — set jira.email in WORKFLOW.md or JIRA_USER env var"}

      !is_binary(api_token()) ->
        {:error, "Jira API token missing — set jira.api_token in WORKFLOW.md or JIRA_TOKEN env var"}

      true ->
        warn_if_cross_project_mode()
        :ok
    end
  end

  defp warn_if_cross_project_mode do
    if project_keys() == [] do
      Logger.warning(
        "Jira config: project_keys is empty — running in CROSS-PROJECT MODE. " <>
          "The trigger is the label \"#{trigger_label()}\" applied to ANY ticket in ANY Jira project " <>
          "this account can read. Make sure that's what you intend."
      )
    end
  end

  defp section_value(key) do
    Map.get(SymphonyElixir.Config.section("jira"), key)
  end

  defp resolve_env_value(nil, fallback), do: fallback

  defp resolve_env_value(value, fallback) when is_binary(value) do
    trimmed = String.trim(value)

    case env_reference_name(trimmed) do
      {:ok, env_name} ->
        case System.get_env(env_name) do
          nil -> fallback
          "" -> nil
          env_value -> env_value
        end

      :error ->
        trimmed
    end
  end

  defp resolve_env_value(_value, fallback), do: fallback

  defp env_reference_name("$" <> env_name) do
    if String.match?(env_name, ~r/^[A-Za-z_][A-Za-z0-9_]*$/) do
      {:ok, env_name}
    else
      :error
    end
  end

  defp env_reference_name(_value), do: :error

  defp normalize_string(value) when is_binary(value) do
    case String.trim(value) do
      "" -> nil
      trimmed -> trimmed
    end
  end

  defp normalize_string(_value), do: nil
end
