defmodule SymphonyElixir.Claude.Config do
  @moduledoc """
  Claude-specific configuration read from the `claude:` YAML section.
  """

  @behaviour SymphonyElixir.AgentConfig

  @default_command "symphony-claude"

  @spec command() :: String.t()
  def command do
    case section_value("command") do
      value when is_binary(value) and value != "" -> String.trim(value)
      _ -> @default_command
    end
  end

  @doc """
  The Claude model to pin the coding agent to (`claude.model` in WORKFLOW.md).

  Returns the trimmed model string — passed through to the `claude` CLI as
  `--model` — or `nil` when unset, in which case Symphony sends no `model`
  field and the `claude` CLI falls back to its own default.
  """
  @spec model() :: String.t() | nil
  def model do
    case section_value("model") do
      value when is_binary(value) and value != "" -> String.trim(value)
      _ -> nil
    end
  end

  @impl SymphonyElixir.AgentConfig
  def validate! do
    if byte_size(String.trim(command())) > 0 do
      :ok
    else
      {:error, "Claude command missing — set claude.command in WORKFLOW.md"}
    end
  end

  defp section_value(key) do
    Map.get(SymphonyElixir.Config.section("claude"), key)
  end
end
