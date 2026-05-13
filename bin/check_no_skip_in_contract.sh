#!/usr/bin/env bash
# L10: no actual @pytest.mark.skip in tests/contract/.
#
# Mirrors tests/contract/test_anti_loopholes.py's L10 check, which uses AST
# parsing to find real decorator nodes. The shell version is a coarser grep
# but excludes the L10 test file itself (which references the marker name
# in its enforcement regex).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/tests/contract"
EXEMPT="$ROOT/tests/contract/test_anti_loopholes.py"

if [[ ! -d "$TARGET" ]]; then
  echo "L10 OK — tests/contract/ does not yet exist."
  exit 0
fi

# Find lines that look like an actual decorator (a `@` at line-start after
# only whitespace), not strings that mention the name.
HITS=$(grep -RInE "^[[:space:]]*@pytest\.mark\.skip" "$TARGET" 2>/dev/null \
       | grep -v "^${EXEMPT}:" || true)

if [[ -n "$HITS" ]]; then
  echo "$HITS"
  echo "L10 violation: @pytest.mark.skip decorator in contract suite." >&2
  exit 1
fi
echo "L10 OK — no skip decorator in contract suite."
