#!/bin/bash
# watch_sessions.sh — cron-based fallback transcript watcher
#
# Some Claude transports (notably the VS Code Claude extension) do not fire
# Stop hooks, so capture_session.sh never runs for them. This script is
# installed as a cron entry by `runaway init` and pulls any transcripts the
# Stop hook missed. The same Python summarizer enforces all 9 guardrails,
# so a malfunctioning cron firing repeatedly can never burn budget.
#
# Recommended crontab line (written by `runaway init --install-watcher`):
#   */10 * * * * /var/www/html/_sMs/RunawayContext_v3/bin/watch_sessions.sh
#
# Environment overrides:
#   RC_KS_DIR — install dir (defaults to ~/_knowledge)
#   RC_RUNAWAY — path to the runaway CLI (auto-detected)

set -uo pipefail

INSTALL_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
LOG_DIR="$INSTALL_DIR/logs"
LOG_FILE="$LOG_DIR/session_watcher.log"
LOCK_FILE="$INSTALL_DIR/sessions_state/_watcher.lock"
mkdir -p "$LOG_DIR" "$INSTALL_DIR/sessions_state" 2>/dev/null || true

log() {
    printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG_FILE" 2>/dev/null
}

# Self-lock — refuse to run concurrently with another instance
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    log "another watcher run is in progress; skipping"
    exit 0
fi

RUNAWAY=""
for candidate in "${RC_RUNAWAY:-}" "$HOME/.local/bin/runaway" "/usr/local/bin/runaway"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        RUNAWAY="$candidate"
        break
    fi
done
if [ -z "$RUNAWAY" ]; then
    RUNAWAY="$(command -v runaway 2>/dev/null || true)"
fi
if [ -z "$RUNAWAY" ]; then
    log "runaway CLI not found — install a symlink or set RC_RUNAWAY"
    exit 0
fi

log "starting sweep"
"$RUNAWAY" --install-dir "$INSTALL_DIR" sessions watch --once >>"$LOG_FILE" 2>&1
log "sweep complete"
exit 0
