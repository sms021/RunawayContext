#!/bin/bash
# capture_session.sh — RunawayContext Stop-hook target
#
# Wired into ~/.claude/settings.json by `runaway init` (or `runaway doctor
# --install-hook`). Claude Code spawns this script when a session stops and
# pipes a JSON event on stdin:
#
#   {"session_id":"...", "transcript_path":"/path/to/transcript.jsonl",
#    "cwd":"/path", "stop_reason":"end_turn"}
#
# The script reads stdin, extracts transcript_path, and dispatches to the
# Python summarizer in the background so the Stop hook returns immediately
# (Claude blocks until Stop hooks finish). The summarizer enforces all 9
# guardrails — this script is a thin shim that NEVER calls a model itself.
#
# Failure of this script must NEVER propagate back to Claude. We exit 0
# unconditionally; the summarizer logs its own errors to:
#   <install_dir>/logs/session_capture.log
#
# Environment overrides:
#   RC_KS_DIR — install dir (defaults to ~/_knowledge)
#   RC_RUNAWAY — path to the runaway CLI (auto-detected)

set -uo pipefail

INSTALL_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
LOG_DIR="$INSTALL_DIR/logs"
LOG_FILE="$LOG_DIR/session_capture.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true

log() {
    printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG_FILE" 2>/dev/null
}

# Read the event JSON from stdin (non-blocking; if nothing arrives we just exit)
EVENT=""
if [ ! -t 0 ]; then
    EVENT="$(cat 2>/dev/null || true)"
fi

if [ -z "$EVENT" ]; then
    log "no event on stdin — exiting cleanly"
    exit 0
fi

# Extract transcript_path with a fallback python parser (jq may not be installed)
TRANSCRIPT="$(
    python3 - <<'PY' <<<"$EVENT" 2>/dev/null
import json, sys
try:
    data = json.loads(sys.stdin.read())
    print(data.get("transcript_path") or "")
except Exception:
    print("")
PY
)"

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    log "no transcript_path in event (or file missing); event=${EVENT:0:200}"
    exit 0
fi

# Resolve the runaway CLI. We prefer an absolute path so this works even when
# the user's PATH doesn't include the venv. Order:
#   1. $RC_RUNAWAY explicit override
#   2. ~/.local/bin/runaway (pipx default)
#   3. /usr/local/bin/runaway (symlink installed by `runaway init`)
#   4. which runaway (last resort — may not exist in the Stop-hook shell env)
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

# Spawn the ingester in the background so the Stop hook returns immediately.
# nohup + disown so the child survives if Claude exits before it finishes.
(
    "$RUNAWAY" --install-dir "$INSTALL_DIR" sessions ingest \
        --transcript "$TRANSCRIPT" >>"$LOG_FILE" 2>&1
) &
disown 2>/dev/null || true

log "dispatched ingest for $TRANSCRIPT"
exit 0
