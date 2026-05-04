#!/usr/bin/env bash
# RunawayContext v2 — cron / launchd drift watcher.
#
# Same check as check_md_drift.sh but designed to run on a schedule (every
# 10 min) instead of on Stop hook. This is the safety net for AI tools where
# Stop hooks don't fire (e.g. VS Code Claude extension), and it produces a
# log file you can tail and a snapshot file a dashboard can scrape.
#
# WIRE-IN:
#
# Linux (cron):
#   crontab -e
#   */10 * * * * /path/to/RunawayContext/bin/md_drift_watcher.sh
#
# macOS (launchd) — create ~/Library/LaunchAgents/com.runawaycontext.driftwatcher.plist:
#   <?xml version="1.0" encoding="UTF-8"?>
#   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
#   <plist version="1.0"><dict>
#     <key>Label</key><string>com.runawaycontext.driftwatcher</string>
#     <key>ProgramArguments</key><array>
#       <string>/path/to/RunawayContext/bin/md_drift_watcher.sh</string>
#     </array>
#     <key>StartInterval</key><integer>600</integer>
#     <key>RunAtLoad</key><true/>
#   </dict></plist>
#   launchctl load ~/Library/LaunchAgents/com.runawaycontext.driftwatcher.plist
#
# Outputs:
#   - $RC_LOG_DIR/md_drift_watcher.log : append-only event log with timestamps
#   - $RC_LOG_DIR/md_drift_snapshot.psv: pipe-separated current-state snapshot
#                                         (cleared when no drift exists; useful
#                                         for dashboards)

set -uo pipefail

KS_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
LOG_DIR="${RC_LOG_DIR:-$HOME/_knowledge/logs}"
DB="$KS_DIR/knowledge.db"

[[ -r "$DB" ]] || exit 0
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/md_drift_watcher.log"
SNAP="$LOG_DIR/md_drift_snapshot.psv"
TS=$(date '+%Y-%m-%d %H:%M:%S')

DRIFT_COUNT=0
TMP=$(mktemp 2>/dev/null || mktemp -t md_drift)
trap "rm -f $TMP" EXIT

while IFS='|' read -r slug md_path cap; do
    [[ -z "$md_path" ]] && continue
    [[ -f "$md_path" ]] || continue
    actual=$(wc -l < "$md_path" | tr -d ' ')
    cap=${cap:-150}
    if [ "$actual" -gt "$cap" ]; then
        echo "[$TS] DRIFT: $md_path is $actual lines (cap $cap, project=$slug)" >> "$LOG"
        echo "$slug|$md_path|$actual|$cap|$TS" >> "$TMP"
        DRIFT_COUNT=$((DRIFT_COUNT + 1))
    fi
done < <(sqlite3 "$DB" "SELECT project_slug, md_path, COALESCE(md_line_cap, 150)
                          FROM project_context_card
                          WHERE md_path IS NOT NULL")

# Update the snapshot atomically — replace if drift exists, clear if not
if [ -s "$TMP" ]; then
    cp "$TMP" "$SNAP"
elif [ -f "$SNAP" ]; then
    : > "$SNAP"
fi

# Hourly heartbeat when clean (so the log doesn't go silent for days)
MIN=$(date '+%M')
if [ "$MIN" = "00" ] && [ "$DRIFT_COUNT" = "0" ]; then
    echo "[$TS] heartbeat: 0 files over cap" >> "$LOG"
fi

exit 0
