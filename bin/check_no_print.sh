#!/usr/bin/env bash
# Discipline: no print() in src/. CLI / stats modules are allowed (explicit
# allowlist below).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
[[ -d "$SRC" ]] || { echo "no-print OK — no src dir yet."; exit 0; }

ALLOWED='cli.py|stats.py|init.py|mcp_server.py|drift.py|reindex_embeddings.py|migrate.py|brief_preview.py|audit.py|doctor.py'

BAD=$(grep -RIn -E '^\s*print\(' "$SRC" 2>/dev/null \
      | grep -vE "($ALLOWED)" || true)

if [[ -n "$BAD" ]]; then
  echo "no-print violation: use logging in src/." >&2
  echo "$BAD" >&2
  exit 1
fi
echo "no-print OK."
