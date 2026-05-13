#!/usr/bin/env bash
# HR-13: shipping code contains no TODO/FIXME/HACK/XXX/"deferred" markers.
#
# Scope mirrors tests/contract/test_hr_13_no_todo_in_release.py:
#   - src/ and templates/ are scanned in full.
#   - tests/ is scanned EXCEPT the HR-13 test file itself, which legitimately
#     references the marker names in regex constants.
#   - docs/ is excluded — HARD_RULES.md and specs/ describe HR-13 by name;
#     the rule's documentation is not a violation of the rule.
#   - Top-level docs that talk about HR-13 by name (README, BOOTSTRAP,
#     RUNAWAYCONTEXT, INSTALL_PROMPT, MIGRATION_V2_TO_V3) are also exempt.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TARGETS=(
  "$ROOT/src"
  "$ROOT/templates"
)

# tests/ minus the HR-13 self-test
TESTS_EXCLUDED="$ROOT/tests/contract/test_hr_13_no_todo_in_release.py"

MARKERS='\b(TODO|FIXME|HACK|XXX)\b'
FOUND=0

for t in "${TARGETS[@]}"; do
  [[ -e "$t" ]] || continue
  if grep -RInE --exclude-dir=__pycache__ --exclude-dir=.git "$MARKERS" "$t" 2>/dev/null; then
    FOUND=1
  fi
done

# Scan tests except the exemption.
if [[ -d "$ROOT/tests" ]]; then
  HITS=$(grep -RInE --exclude-dir=__pycache__ "$MARKERS" "$ROOT/tests" 2>/dev/null \
         | grep -v "^${TESTS_EXCLUDED}:" || true)
  if [[ -n "$HITS" ]]; then
    echo "$HITS"
    FOUND=1
  fi
fi

if [[ $FOUND -eq 1 ]]; then
  echo "HR-13 violation: TODO/FIXME/HACK/XXX markers found in shipping code." >&2
  exit 1
fi
echo "HR-13 OK — no TODO/FIXME/HACK/XXX markers in src/tests/templates."
