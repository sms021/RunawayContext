#!/usr/bin/env bash
# RunawayContext v3 — clean-install verifier (HR-15).
#
# Runs INSIDE the Docker container produced by ./Dockerfile. The image has
# the package already installed (build-time `pip install -e .[dev]`); this
# script exercises the public CLI surface against an ephemeral install
# directory at /tmp/rc_install so no developer state leaks in.
#
# Steps (any non-zero exit aborts the script):
#   1. Contract suite must pass.
#   2. `runaway db migrate` against the ephemeral DBs must succeed.
#   3. `runaway slug register tooling` must succeed.
#   4. `runaway list-lessons` (smoke; empty list is fine) must succeed.
#
# Exit 0 only when all four steps succeed.
set -euo pipefail

export RC_KS_DIR=/tmp/rc_install
mkdir -p "$RC_KS_DIR"

echo "==> [1/4] Running contract suite (HR-1..HR-15)"
python -m pytest -m contract -v
echo "    contract suite OK"

echo "==> [2/4] Running runaway db migrate against ephemeral DBs"
runaway db migrate \
    --knowledge-db "$RC_KS_DIR/knowledge.db" \
    --sessions-db "$RC_KS_DIR/sessions.db" \
    --metrics-db "$RC_KS_DIR/metrics.db"
echo "    migrate OK"

echo "==> [3/4] Registering canonical slug 'tooling'"
runaway slug register tooling
echo "    slug register OK"

echo "==> [4/4] Smoke-testing runaway list-lessons"
runaway list-lessons
echo "    list-lessons OK"

echo "==> HR-15 clean-install verifier completed successfully."
