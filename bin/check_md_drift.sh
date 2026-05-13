#!/usr/bin/env bash
# Stop-hook drift detector (HR-5).
# Reads project_context_card rows, checks each md_path against md_line_cap.
set -euo pipefail

DB="${RC_KNOWLEDGE_DB:-$HOME/_knowledge/knowledge.db}"
LOG="${RC_DRIFT_LOG:-$HOME/_knowledge/drift.log}"
mkdir -p "$(dirname "$LOG")"

[[ -f "$DB" ]] || { echo "drift: knowledge.db not found at $DB"; exit 0; }

sqlite3 "$DB" "SELECT project, md_path, md_line_cap FROM project_context_card WHERE md_path IS NOT NULL" |
while IFS='|' read -r project md_path cap; do
  [[ -f "$md_path" ]] || continue
  cap="${cap:-150}"
  lines=$(wc -l < "$md_path")
  if (( lines > cap )); then
    msg="DRIFT [$project]: $md_path is $lines lines (cap $cap)"
    echo "$msg"
    echo "$(date -Iseconds) $msg" >> "$LOG"
  fi
done
