#!/usr/bin/env bash
#
# tail-log.sh — show the last N timestamped log entries from Symphony's
# disk_log file, deduped and sorted chronologically.
#
# Symphony writes structured logs to `log/symphony.log.1` via Erlang's
# disk_log (wrap log format — a circular buffer rewritten in place).
# Because of the wrap format, `strings | tail` returns entries in physical
# byte order, not chronological order — old entries can appear AFTER new
# entries in the dump. We extract lines starting with an ISO timestamp,
# dedupe them, sort, and show the last N. Refresh every 2s.

set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")")"

LOG_FILE="log/symphony.log.1"
LINES_TO_SHOW="${1:-25}"

if [ ! -f "$LOG_FILE" ]; then
  echo "FATAL: $LOG_FILE not found. Has Symphony been started?" >&2
  exit 1
fi

PREV_LAST=""

while true; do
  # Extract only lines starting with ISO timestamp, dedupe, sort, take last N.
  CURRENT="$(strings "$LOG_FILE" \
    | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' \
    | sort -u \
    | tail -"$LINES_TO_SHOW")"

  CURRENT_LAST="$(printf '%s\n' "$CURRENT" | tail -1)"

  if [ "$CURRENT_LAST" != "$PREV_LAST" ]; then
    # Try `clear` if TERM is set; otherwise just print a separator
    if [ -n "${TERM:-}" ] && command -v clear >/dev/null 2>&1; then
      clear
    else
      printf '\n\n=================================================\n'
    fi
    echo "[tail-log] $LOG_FILE — last $LINES_TO_SHOW entries chronologically (refresh every 2s, Ctrl+C to stop)"
    echo ""
    printf '%s\n' "$CURRENT"
    PREV_LAST="$CURRENT_LAST"
  fi

  sleep 2
done
