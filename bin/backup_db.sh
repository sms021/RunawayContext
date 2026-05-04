#!/usr/bin/env bash
# RunawayContext v2 — dated DB snapshot.
#
# Snapshots knowledge.db (and optionally sessions.db) before any bulk operation.
# Uses SQLite's `.backup` for a consistent online snapshot — safe to run while
# the DB is in active use.
#
# Usage:
#   ./bin/backup_db.sh              # both DBs, label=manual
#   ./bin/backup_db.sh pre-cleanup  # custom label
#   ./bin/backup_db.sh --knowledge-only       # just knowledge.db
#
# Backups land in: $RC_BACKUP_DIR (default: $RC_KS_DIR/backups)
# Auto-prunes to last 30 backups (configurable via RC_BACKUP_KEEP).
#
# Cross-platform — works on macOS + Linux.

set -euo pipefail

KS_DIR="${RC_KS_DIR:-$HOME/_knowledge}"
BACKUP_DIR="${RC_BACKUP_DIR:-$KS_DIR/backups}"
BACKUP_KEEP="${RC_BACKUP_KEEP:-30}"

KNOWLEDGE_ONLY=0
LABEL="manual"
for arg in "$@"; do
    case "$arg" in
        --knowledge-only) KNOWLEDGE_ONLY=1 ;;
        --sessions-only) SESSIONS_ONLY=1 ;;
        --help|-h)
            grep '^#' "$0" | head -20 | sed 's/^# \?//'
            exit 0 ;;
        *) LABEL="$arg" ;;
    esac
done

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)

backup_one() {
    local name="$1"
    local src="$KS_DIR/${name}.db"
    [[ -f "$src" ]] || { echo "  (skip ${name}.db — not found)"; return; }
    local dst="$BACKUP_DIR/${name}.db.${LABEL}.${TS}.bak"
    sqlite3 "$src" ".backup '$dst'"
    echo "  ✓ $dst"
}

echo "Backing up to $BACKUP_DIR/ (label='$LABEL')"
backup_one knowledge
[[ "$KNOWLEDGE_ONLY" -eq 0 ]] && backup_one sessions

# Prune to last N — sort by mtime, drop the oldest
if [ "$BACKUP_KEEP" -gt 0 ]; then
    # ls -1t lists newest first; keep first N, delete rest
    ls -1t "$BACKUP_DIR"/*.bak 2>/dev/null | tail -n "+$((BACKUP_KEEP + 1))" | xargs -r rm -f
fi
