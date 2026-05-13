#!/usr/bin/env bash
# HR-10: no silent except.
# Flags `except [...]: pass` and `except [...]: return None` patterns.
# The telemetry emit() body is explicitly allowed via a `# emit-allowed` comment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
[[ -d "$SRC" ]] || { echo "HR-10 OK — no src dir yet."; exit 0; }

# Grep for the bad pattern; strip allowed lines.
BAD=$(grep -RIn -E '^[[:space:]]+except[^:]*:[[:space:]]*(pass|return None)' "$SRC" 2>/dev/null \
      | grep -v 'emit-allowed' || true)

if [[ -n "$BAD" ]]; then
  echo "HR-10 violation: silent except found." >&2
  echo "$BAD" >&2
  exit 1
fi
echo "HR-10 OK — no silent except."
