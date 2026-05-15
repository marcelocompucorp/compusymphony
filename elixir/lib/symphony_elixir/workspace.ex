defmodule SymphonyElixir.Workspace do
  @moduledoc """
  Creates isolated per-issue workspaces for parallel Codex agents.
  """

  require Logger
  alias SymphonyElixir.Config

  @excluded_entries MapSet.new([".elixir_ls", "tmp"])

  @type preflight_decision ::
          :proceed_create
          | :proceed_clean
          | {:refuse, :inflight, Path.t()}
          | {:refuse, :orphan_branch, Path.t(), String.t()}
          | {:refuse, :already_done, Path.t(), String.t()}

  @doc """
  Inspect the on-disk workspace state for an issue BEFORE dispatching the
  agent, and decide whether it's safe to proceed.

  This guards against two real failure modes the original `create_for_issue`
  couldn't see:

  1. **In-flight or just-killed run**: another agent (or the same one after
     a Symphony crash) has the workspace in active use. Cleaning would race;
     proceeding would corrupt. Returns `{:refuse, :inflight, _}`.
  2. **Orphan branch**: a previous run pushed commits to a local
     `agent/*-fix` branch but never published an upstream (or the upstream
     was deleted). Cleaning silently loses the diff. Returns
     `{:refuse, :orphan_branch, _, branch_name}` so the operator can recover
     the branch before we delete the workspace.

  All other states (no workspace, half-created workspace, clean .git that's
  in sync with origin, dirty tree older than the agent-stall threshold)
  return `:proceed_create` or `:proceed_clean` — callers should remove the
  workspace and recreate it as usual.

  The "in-flight" mtime threshold matches `Config.agent_stall_timeout_ms/0`
  so this and the orchestrator agree on what "active" means.
  """
  @spec preflight_check(map() | String.t() | nil) :: preflight_decision()
  def preflight_check(issue_or_identifier) do
    issue_context = issue_context(issue_or_identifier)
    safe_id = safe_identifier(issue_context.issue_identifier)
    workspace = workspace_path_for_issue(safe_id)
    do_preflight_check(workspace)
  end

  defp do_preflight_check(workspace) do
    repo_dir = Path.join(workspace, "repo")
    git_dir = Path.join(repo_dir, ".git")
    done_file = Path.join(workspace, "AGENT_DONE")

    cond do
      File.exists?(done_file) ->
        content = File.read!(done_file) |> String.trim()
        {:refuse, :already_done, workspace, content}

      not File.dir?(workspace) ->
        :proceed_create

      not File.dir?(repo_dir) ->
        :proceed_clean

      not File.exists?(git_dir) ->
        :proceed_clean

      true ->
        classify_git_state(workspace, repo_dir)
    end
  end

  defp classify_git_state(workspace, repo_dir) do
    dirty? = git_dirty?(repo_dir)
    ahead? = git_commits_ahead?(repo_dir)
    agent_branch = git_orphan_agent_branch(repo_dir)
    recent? = workspace_recently_touched?(repo_dir)

    cond do
      # 1. An agent/*-fix branch exists with commits not reachable from any
      # remote ref. Cleaning would lose operator data regardless of what HEAD
      # is currently checked out to. (Earlier this clause also required the
      # CURRENT branch to have no upstream — that was wrong: if HEAD is
      # master tracking origin/master, the orphan agent branch would slip
      # past. `branch_has_unpublished_commits?` already does the correct
      # per-branch check via `rev-list <branch> --not --remotes`.)
      agent_branch != nil ->
        {:refuse, :orphan_branch, workspace, agent_branch}

      # 2. Live agent run (or just-killed): refuse to touch.
      (dirty? or ahead?) and recent? ->
        {:refuse, :inflight, workspace}

      # 3. Everything else is safe to clean and re-create:
      #    - dirty tree older than the stall threshold (abandoned run)
      #    - clean tree with no commits ahead (PR merged, branch gone)
      #    - clean tree, no upstream tracking, no agent branch (PR merged + delete-on-merge)
      true ->
        :proceed_clean
    end
  end

  defp git_dirty?(repo_dir) do
    case System.cmd("git", ["-C", repo_dir, "status", "--porcelain"], stderr_to_stdout: true) do
      {output, 0} -> String.trim(output) != ""
      _ -> false
    end
  end

  defp git_commits_ahead?(repo_dir) do
    case System.cmd("git", ["-C", repo_dir, "rev-list", "--count", "@{u}..HEAD"], stderr_to_stdout: true) do
      {output, 0} ->
        case Integer.parse(String.trim(output)) do
          {n, _} when n > 0 -> true
          _ -> false
        end

      _ ->
        false
    end
  end

  # Look for any local branch matching `agent/*-fix` that has commits not
  # reachable from any remote ref. If any such branch exists, treat it as
  # an orphan (operator might want to recover before we wipe).
  defp git_orphan_agent_branch(repo_dir) do
    with {branches_output, 0} <-
           System.cmd("git", ["-C", repo_dir, "for-each-ref", "--format=%(refname:short)", "refs/heads/agent/"], stderr_to_stdout: true) do
      branches_output
      |> String.split("\n", trim: true)
      |> Enum.find(fn branch -> branch_has_unpublished_commits?(repo_dir, branch) end)
    else
      _ -> nil
    end
  end

  defp branch_has_unpublished_commits?(repo_dir, branch) do
    case System.cmd("git", ["-C", repo_dir, "rev-list", "--count", branch, "--not", "--remotes"], stderr_to_stdout: true) do
      {output, 0} ->
        case Integer.parse(String.trim(output)) do
          {n, _} when n > 0 -> true
          _ -> false
        end

      _ ->
        false
    end
  end

  defp workspace_recently_touched?(repo_dir) do
    stall_ms = Config.agent_stall_timeout_ms()

    case File.stat(repo_dir, time: :posix) do
      {:ok, %File.Stat{mtime: mtime}} ->
        age_ms = (System.os_time(:second) - mtime) * 1_000
        age_ms < stall_ms

      _ ->
        false
    end
  end

  @spec create_for_issue(map() | String.t() | nil) :: {:ok, Path.t()} | {:error, term()}
  def create_for_issue(issue_or_identifier) do
    issue_context = issue_context(issue_or_identifier)

    try do
      safe_id = safe_identifier(issue_context.issue_identifier)

      workspace = workspace_path_for_issue(safe_id)

      with :ok <- validate_workspace_path(workspace),
           {:ok, created?} <- ensure_workspace(workspace),
           :ok <- maybe_run_after_create_hook(workspace, issue_context, created?) do
        {:ok, workspace}
      end
    rescue
      error in [ArgumentError, ErlangError, File.Error] ->
        Logger.error("Workspace creation failed #{issue_log_context(issue_context)} error=#{Exception.message(error)}")
        {:error, error}
    end
  end

  defp ensure_workspace(workspace) do
    cond do
      File.dir?(workspace) ->
        clean_tmp_artifacts(workspace)
        {:ok, false}

      File.exists?(workspace) ->
        File.rm_rf!(workspace)
        create_workspace(workspace)

      true ->
        create_workspace(workspace)
    end
  end

  defp create_workspace(workspace) do
    File.rm_rf!(workspace)
    File.mkdir_p!(workspace)
    {:ok, true}
  end

  @spec remove(Path.t()) :: {:ok, [String.t()]} | {:error, term(), String.t()}
  def remove(workspace) do
    case File.exists?(workspace) do
      true ->
        case validate_workspace_path(workspace) do
          :ok ->
            maybe_run_before_remove_hook(workspace)
            File.rm_rf(workspace)

          {:error, reason} ->
            {:error, reason, ""}
        end

      false ->
        File.rm_rf(workspace)
    end
  end

  @spec remove_issue_workspaces(term()) :: :ok
  def remove_issue_workspaces(identifier) when is_binary(identifier) do
    safe_id = safe_identifier(identifier)
    workspace = workspace_path_for_issue(safe_id)

    remove(workspace)
    :ok
  end

  def remove_issue_workspaces(_identifier) do
    :ok
  end

  @spec run_before_run_hook(Path.t(), map() | String.t() | nil) :: :ok | {:error, term()}
  def run_before_run_hook(workspace, issue_or_identifier) when is_binary(workspace) do
    issue_context = issue_context(issue_or_identifier)

    case Config.workspace_hooks()[:before_run] do
      nil ->
        :ok

      command ->
        run_hook(command, workspace, issue_context, "before_run")
    end
  end

  @spec run_after_run_hook(Path.t(), map() | String.t() | nil) :: :ok
  def run_after_run_hook(workspace, issue_or_identifier) when is_binary(workspace) do
    issue_context = issue_context(issue_or_identifier)

    case Config.workspace_hooks()[:after_run] do
      nil ->
        :ok

      command ->
        run_hook(command, workspace, issue_context, "after_run")
        |> ignore_hook_failure()
    end
  end

  defp workspace_path_for_issue(safe_id) when is_binary(safe_id) do
    case Config.tracker_kind() do
      "github" ->
        repo = SymphonyElixir.GitHub.Config.repo() || ""
        Path.join([Config.workspace_root(), repo, safe_id])

      _ ->
        Path.join(Config.workspace_root(), safe_id)
    end
  end

  defp safe_identifier(identifier) do
    String.replace(identifier || "issue", ~r/[^a-zA-Z0-9._-]/, "_")
  end

  defp clean_tmp_artifacts(workspace) do
    Enum.each(MapSet.to_list(@excluded_entries), fn entry ->
      File.rm_rf(Path.join(workspace, entry))
    end)
  end

  defp maybe_run_after_create_hook(workspace, issue_context, created?) do
    case created? do
      true ->
        case Config.workspace_hooks()[:after_create] do
          nil ->
            :ok

          command ->
            run_hook(command, workspace, issue_context, "after_create")
        end

      false ->
        :ok
    end
  end

  defp maybe_run_before_remove_hook(workspace) do
    case File.dir?(workspace) do
      true ->
        case Config.workspace_hooks()[:before_remove] do
          nil ->
            :ok

          command ->
            run_hook(
              command,
              workspace,
              %{issue_id: nil, issue_identifier: Path.basename(workspace)},
              "before_remove"
            )
            |> ignore_hook_failure()
        end

      false ->
        :ok
    end
  end

  defp ignore_hook_failure(:ok), do: :ok
  defp ignore_hook_failure({:error, _reason}), do: :ok

  defp run_hook(command, workspace, issue_context, hook_name) do
    timeout_ms = Config.workspace_hooks()[:timeout_ms]

    Logger.info("Running workspace hook hook=#{hook_name} #{issue_log_context(issue_context)} workspace=#{workspace}")

    task =
      Task.async(fn ->
        System.cmd("sh", ["-lc", command], cd: workspace, stderr_to_stdout: true)
      end)

    case Task.yield(task, timeout_ms) do
      {:ok, cmd_result} ->
        handle_hook_command_result(cmd_result, workspace, issue_context, hook_name)

      nil ->
        Task.shutdown(task, :brutal_kill)

        Logger.warning("Workspace hook timed out hook=#{hook_name} #{issue_log_context(issue_context)} workspace=#{workspace} timeout_ms=#{timeout_ms}")

        {:error, {:workspace_hook_timeout, hook_name, timeout_ms}}
    end
  end

  defp handle_hook_command_result({_output, 0}, _workspace, _issue_id, _hook_name) do
    :ok
  end

  defp handle_hook_command_result({output, status}, workspace, issue_context, hook_name) do
    sanitized_output = sanitize_hook_output_for_log(output)

    Logger.warning("Workspace hook failed hook=#{hook_name} #{issue_log_context(issue_context)} workspace=#{workspace} status=#{status} output=#{inspect(sanitized_output)}")

    {:error, {:workspace_hook_failed, hook_name, status, output}}
  end

  defp sanitize_hook_output_for_log(output, max_bytes \\ 2_048) do
    binary_output = IO.iodata_to_binary(output)

    case byte_size(binary_output) <= max_bytes do
      true ->
        binary_output

      false ->
        binary_part(binary_output, 0, max_bytes) <> "... (truncated)"
    end
  end

  defp validate_workspace_path(workspace) when is_binary(workspace) do
    expanded_workspace = Path.expand(workspace)
    root = Path.expand(Config.workspace_root())
    root_prefix = root <> "/"

    cond do
      expanded_workspace == root ->
        {:error, {:workspace_equals_root, expanded_workspace, root}}

      String.starts_with?(expanded_workspace <> "/", root_prefix) ->
        ensure_no_symlink_components(expanded_workspace, root)

      true ->
        {:error, {:workspace_outside_root, expanded_workspace, root}}
    end
  end

  defp ensure_no_symlink_components(workspace, root) do
    workspace
    |> Path.relative_to(root)
    |> Path.split()
    |> Enum.reduce_while(root, fn segment, current_path ->
      next_path = Path.join(current_path, segment)

      case File.lstat(next_path) do
        {:ok, %File.Stat{type: :symlink}} ->
          {:halt, {:error, {:workspace_symlink_escape, next_path, root}}}

        {:ok, _stat} ->
          {:cont, next_path}

        {:error, :enoent} ->
          {:halt, :ok}

        {:error, reason} ->
          {:halt, {:error, {:workspace_path_unreadable, next_path, reason}}}
      end
    end)
    |> case do
      :ok -> :ok
      {:error, _reason} = error -> error
      _final_path -> :ok
    end
  end

  defp issue_context(%{id: issue_id, identifier: identifier}) do
    %{
      issue_id: issue_id,
      issue_identifier: identifier || "issue"
    }
  end

  defp issue_context(identifier) when is_binary(identifier) do
    %{
      issue_id: nil,
      issue_identifier: identifier
    }
  end

  defp issue_context(_identifier) do
    %{
      issue_id: nil,
      issue_identifier: "issue"
    }
  end

  defp issue_log_context(%{issue_id: issue_id, issue_identifier: issue_identifier}) do
    "issue_id=#{issue_id || "n/a"} issue_identifier=#{issue_identifier || "issue"}"
  end
end
