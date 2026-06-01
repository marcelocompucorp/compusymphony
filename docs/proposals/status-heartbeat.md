# Proposal: fix the frozen dashboard during long dev-site waits

**Status:** draft for review (round 3 — incorporates reviewer r1 + r2 corrections)

> **r2 critical finding (folded in below):** writing a heartbeat *file* emits no codex event. The dashboard payload only reloads on codex-driven `:observability_updated` (not on `:runtime_tick`), and the 5-min stall detector also keys off codex activity. So a file-only heartbeat is (a) never re-read during the silent wait and (b) does not stop the stall-kill. **R1 (stall) and R2 (refresh) share one root: the heartbeat must emit a real orchestrator liveness signal, not just write a file.** Fixes C and A are revised accordingly.
**Trigger:** IESBUILD-232 run on 2026-06-01 — dashboard showed `step 6 / 15 — "Plan written, core fix implemented and committed"` frozen for ~40 min while the agent was actually in dev-site Phase B.

---

## 1. Root cause (verified against code)

Three layers, only the first of which round-1 of this proposal addressed correctly.

### 1a. Status emission is agent-driven, best-effort, unenforced
`WORKFLOW.md` §"Step reporting" (~L1049) tells the agent, in prose, to `echo '{"step": N, ...}' > .symphony-status` "at the start of each numbered step." Nothing in code emits or enforces it. Trace of the IESBUILD-232 run shows writes at step 1 / 3 / 6 then silence for the rest of the run. The audit (`analyze-run.py`) never reads `.symphony-status`, so a missing emit is invisible.

### 1b. The "15 steps" scheme is invented per-run
`WORKFLOW.md` hard-codes exactly ONE concrete status line (`{"step": 13, "total": 15, ...}` at ~L825) plus the placeholder template. The actual routine runs steps 0–15. So the agent paraphrases its own step numbers/labels (e.g. step 6 = "plan + fix + commit", three routine steps lumped). Numbers are not comparable across runs.

### 1c. The longest wait is a single blocking call with no internal checkpoint — AND it is the wrong function
- `poll_until_deployed` (repro_helpers.py ~L851) Phase-2 loop: `time.sleep(30)` ticks, no print/write.
- **But the dominant wait is `wait_until_site_up` (repro_helpers.py L1196), `timeout_s=900` (15 min), `poll_interval_s=20`, also silent.** Its own docstring: *"the containers still need ~5–15 min to start, run drush updb… the gap between Jenkins job finished and site actually serves requests."*
- In the agent-authored `devsite_phase_a.py`, `poll_until_deployed` is chunked (`timeout_s=90`, re-invoked by a bash `until` loop), but `wait_until_site_up` is called **once, un-chunked**, inside the same process. So the stall-detector/re-invoke pattern protects the Jenkins-poll phase but **not** the site-warm-up phase — which is the longest.

**Round-1 error:** the original proposal instrumented `poll_until_deployed` only. That would still leave the dashboard frozen through `wait_until_site_up`. (Caught by reviewer r1.)

### 1d. NEW — the dashboard actively *suppresses* the liveness it already has
`dashboard_live.ex` L191:
```elixir
<%= case entry.step_info do %>
  <% %{step: step, total: total, label: label} -> %>   # shows ONLY step badge + label + pips
  <% nil -> %>                                          # shows last_event / last_message / last_event_at
```
When `.symphony-status` exists (even if stale), `step_info` is non-nil, so the view takes the first branch and **hides** `last_event_at` — the very liveness signal that would otherwise show the agent is alive. A present-but-stale status file is therefore *worse* than no file: it masks the fallback. This is a dashboard bug independent of the agent.

### 1e. NEW — the functional twin: the 5-min stall detector
`config.ex` `@default_agent_stall_timeout_ms = 300_000` (5 min). A long *foreground-blocking* Bash wait emits no codex events, so Symphony declares the agent stalled at 5 min and **kills + restarts** it (observed repeatedly in the May runs: `stalled for 331724ms without codex activity`). The 2026-06-01 run avoided this by using `ScheduleWakeup` + a background readiness check (agent yields instead of blocking) — likely part of the in-flight Symphony changes being e2e-tested. The heartbeat work below is observability; this note records that the *blocking-wait* anti-pattern has a functional cost too, and the fix should prefer the yield/notify pattern over foreground blocking.

---

## 2. The design tension round-1 + reviewer-r1 both missed

Reviewer r1 said "put the heartbeat in the phase script, not the generic helper." **But the phase script is not versioned** — `prompts/devsite_phase_a.py` does not exist in the repo; the agent writes it fresh each run from `WORKFLOW.md` templates. So "put it in the phase script" = rely on the agent to include it = unenforced again (cause 1a).

Resolution: the heartbeat must live in **committed code that every phase script imports** — i.e. `repro_helpers.py` — but exposed so the generic poll loops don't hard-code Symphony dashboard paths. The seam is an **optional `on_tick` callback** (generic) that the Symphony side supplies as a heartbeat writer. This satisfies both objections at once:
- enforcement: the loop logic is committed and runs whenever the helper is called;
- layering: the generic helper knows nothing about `.symphony-heartbeat` — it just calls `on_tick(elapsed_s)` if given.

---

## 3. Proposed design

### Fix A — `on_tick` callback in the committed poll/wait helpers (the real fix)
Add `on_tick: Callable[[int], None] | None = None` to **`poll_until_deployed`, `poll_until_released`, AND `wait_until_site_up`** (the last is the long pole). Call `on_tick(elapsed_s)` once per loop iteration. Generic, backward-compatible (default `None` = today's behaviour, so the one-shot/test callers at the documented call sites are unaffected).

```python
# repro_helpers.py — wait_until_site_up (the missed long pole)
 def wait_until_site_up(
     hostname: str,
     *,
     timeout_s: int = 900,
     poll_interval_s: int = 20,
+    on_tick=None,            # called each poll with elapsed seconds (int)
 ) -> None:
     ...
     start = time.monotonic()
     deadline = start + timeout_s
     while time.monotonic() < deadline:
         try:
             r = requests.get(url, auth=auth, timeout=15, allow_redirects=True)
             if r.status_code == 200:
                 return
         except Exception:
             pass
+        if on_tick:
+            try: on_tick(int(time.monotonic() - start))
+            except Exception: pass   # heartbeat must never break the wait
         time.sleep(poll_interval_s)
```
Identical `on_tick` insertion in the `poll_until_deployed` (L851) and `poll_until_released` (L1051) loops.

### Fix B — a committed Symphony heartbeat writer (imported by phase scripts)
New committed helper (e.g. `prompts/symphony_status.py`) so the agent-authored phase script is a one-line import, not hand-rolled logic:
```python
import json, os, pathlib, time

def write_heartbeat(phase: str, *, waiting_on: str | None = None,
                    elapsed_s: int | None = None, state: str = "running",
                    path: str = ".symphony-heartbeat") -> None:
    """Atomic, stateless liveness ping. Separate file from .symphony-status
    to avoid two-writer races with the agent's step writes."""
    payload = {"phase": phase, "state": state, "ts": int(time.time())}
    if waiting_on is not None: payload["waiting_on"] = waiting_on
    if elapsed_s is not None: payload["elapsed_s"] = elapsed_s
    tmp = f"{path}.tmp"
    pathlib.Path(tmp).write_text(json.dumps(payload))
    os.replace(tmp, path)            # atomic vs the dashboard reader
```
WORKFLOW.md Phase-A/B templates then become:
```python
from symphony_status import write_heartbeat
write_heartbeat("dev-site Phase A: Jenkins build", waiting_on="jenkins")     # at main() entry, every re-invoke
...
wait_until_site_up(host, on_tick=lambda s: write_heartbeat(
    "dev-site Phase A: site warm-up", waiting_on=host, elapsed_s=s))
```
Key properties (addressing reviewer r1 risks):
- **Separate file** `.symphony-heartbeat` — no race with the agent's `.symphony-status` step writes (reviewer risk #2).
- **Stateless** — always writes on entry; survives session resume (reviewer risk #5).
- **Failure transition** — on `RuntimeError`/`TimeoutError` the phase script's `except` writes `state="blocked"` so the dashboard shows blocked, not a frozen "waiting 12m" (reviewer risk #4).
- **No build-number parse required** in the hot loop — `waiting_on="jenkins"` is enough; the build URL is already cached in `.devsite-build-url` if the dashboard wants detail (reviewer risk #3).

### Fix C — dashboard surfaces liveness even when a step file is present (fixes 1d) + periodic reload (fixes R2)
`presenter.ex`: read `.symphony-heartbeat` alongside `.symphony-status`. **Note: there is no `root()` helper** — inline the same resolution `read_step_info` uses (presenter.ex:176):
```elixir
# presenter.ex
defp read_heartbeat(identifier) when is_binary(identifier) do
  root = Application.get_env(:symphony_elixir, :workspace_root) || Config.workspace_root()
  path = Path.join([root, identifier, ".symphony-heartbeat"])
  with {:ok, c} <- File.read(path), {:ok, %{"ts" => ts} = hb} <- Jason.decode(c) do
    %{phase: hb["phase"], state: hb["state"], waiting_on: hb["waiting_on"], ts: ts}
  else _ -> nil end
end
```
Compute `stale` in the **view**, not at read time, so it re-evaluates against `@now` each tick.
`dashboard_live.ex`: render the heartbeat line in BOTH branches (step present or nil) so a frozen step never hides liveness.

**R2 (critical) — the payload must reload independent of codex events.** Today `load_payload()` re-runs only on `:observability_updated` (codex-driven). During a silent `wait_until_site_up` it never fires, so a freshly-written heartbeat file is never re-read. Add a periodic reload: either call `load_payload()` inside the existing `:runtime_tick` (raise its interval if needed) or add a slow `:reload_payload` tick (~5–10s). **This dashboard change alone unfreezes the view with zero agent cooperation** — it un-suppresses `last_event_at` (1d) and re-reads any heartbeat. It is the highest-leverage single shippable.

### Fix D — drop hand-typed step numbers in favour of phase names (replaces r1 Fix 3)
Per reviewer r1: numeric `step/total` is cosmetic and drift-prone (3 sync points). Prefer stable named phases (`investigating`, `implementing`, `reviewing`, `dev-site Phase A`, `dev-site Phase B`, `opening PR`). If a number is wanted for an ordered UI, derive it from the phase, never hand-type. Lower priority than A–C.

### Fix E — audit check (optional, from r1 Fix 4)
`analyze-run.py`: model on `detect_jenkins_writes` (greps bash_commands from the transcript, not the on-disk file which is overwritten). Flag: reached a dev-site trigger but emitted no heartbeat/status between last commit and `AGENT_DONE`.

---

## 4. Highest-leverage shippable (revised per r2)
**Ship in this order:**
1. **Fix C (dashboard) first** — render liveness in both branches + periodic payload reload. Needs **no agent cooperation**, directly kills 1d, and re-reads any heartbeat. If only one thing ships, this is it.
2. **Fix A + B**, but with the r2 fix: `on_tick` must emit a **real orchestrator liveness signal** (the same path that calls `notify_dashboard`/resets the stall timer), not just write a file — otherwise the agent is still stall-killed (R1) and the file is never re-read (R2). The two problems share this root.
3. **Blocked-state in committed code (R4):** move the failure→`state="blocked"` write out of the agent-authored phase script into a committed driver (e.g. `symphony_status.py` context manager `with heartbeat_phase(...)` that writes `blocked` on exception, or a committed `devsite_wait.py` the agent invokes as a black box). Don't rely on the agent wiring `try/except` correctly.

D (named phases) and E (audit) are follow-ups.

## 5. Open questions for the reviewer
1. Is `on_tick` the right seam, or should the Symphony heartbeat writer be passed as an object/partial? Any concern that three helpers gaining one optional kwarg is still too much generic-helper surface?
2. Given the phase script is agent-authored, is `prompts/symphony_status.py` (committed, imported) the right enforcement boundary — or should the dev-site wait be lifted out of the agent-authored script into a committed driver entirely?
3. Fix C doubles the workspace file reads per dashboard refresh (status + heartbeat per running session). Acceptable, or merge into one read?
4. Should we also raise the 5-min stall timeout for the dev-site phase specifically (1e), or is the yield/notify pattern (ScheduleWakeup + background check) the canonical answer and the timeout left alone?
5. Anything still missed across the agent ↔ helper ↔ dashboard ↔ audit boundary.
