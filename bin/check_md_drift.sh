#!/usr/bin/env bash
# RunawayContext v2 — drift detector for project CLAUDE.md files.
#
# Walks every project_context_card row that has md_path set, checks the actual
# file's line count against the card's md_line_cap, and warns to STDERR if any
# file has drifted past the cap.
#
# WIRE-IN: install as a Stop hook in your AI tool's settings so it runs at
# session end. Cross-platform (Mac + Linux).
#
# Claude Code (CLI) — add to ~/.claude/settings.json:
#   "hooks": {
#     "Stop": [{
#       "hooks": [{ "type": "command", "command": "/path/to/RunawayContext/bin/check_md_drift.sh" }]
#     }]
#   }
#
# Other AI tools — see your tool's hook documentation. The script is also
# safe to run from a cron / launchd schedule (see md_drift_watcher.sh for that
# pattern, useful when the AI tool doesn't fire Stop hooks).
#
# Exit code: always 0 (non-blocking — goal is visibility, not failure).
# Output: stderr only.

set -uo pipefail

# Resolve knowledge.db location (RC_KS_DIR env var or default ~/_knowledge)
KS_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
DB="$KS_DIR/knowledge.db"

[[ -r "$DB" ]] || exit 0   # silently no-op if no DB yet

WARN_COUNT=0

while IFS='|' read -r slug md_path cap; do
    [[ -z "$md_path" ]] && continue
    [[ -f "$md_path" ]] || continue
    actual=$(wc -l < "$md_path" | tr -d ' ')
    cap=${cap:-150}
    if [ "$actual" -gt "$cap" ]; then
        echo "⚠ CLAUDE.md drift: $md_path is $actual lines (cap $cap, project=$slug)" >&2
        echo "  → regen: python3 lib/ll_brief.py --rebuild-md $slug" >&2
        WARN_COUNT=$((WARN_COUNT + 1))
    fi
done < <(sqlite3 "$DB" "SELECT project_slug, md_path, COALESCE(md_line_cap, 150)
                          FROM project_context_card
                          WHERE md_path IS NOT NULL")

if (( WARN_COUNT > 0 )); then
    echo "" >&2
    echo "$WARN_COUNT project CLAUDE.md file(s) over their cap." >&2
    echo "Hand-edits OUTSIDE the PRESERVE_START / PRESERVE_END markers will be wiped on next regen." >&2
fi

exit 0
