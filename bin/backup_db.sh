#!/usr/bin/env bash
# Dated SQLite snapshot. Uses .backup (consistent online snapshot).
# Auto-prunes to last 30.
set -euo pipefail

KS_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
DB="$KS_DIR/knowledge.db"
SNAP_DIR="$KS_DIR/backups"
KEEP=30

[[ -f "$DB" ]] || { echo "backup: $DB missing"; exit 1; }
mkdir -p "$SNAP_DIR"

stamp=$(date +%Y%m%d_%H%M%S)
out="$SNAP_DIR/knowledge.db.$stamp"
sqlite3 "$DB" ".backup '$out'"
echo "backed up to $out"

# Prune oldest backups beyond KEEP
ls -1t "$SNAP_DIR"/knowledge.db.* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
