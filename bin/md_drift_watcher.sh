#!/usr/bin/env bash
# Cron / launchd watcher — calls check_md_drift.sh every cycle.
# Recommended cron: */10 * * * * /path/to/md_drift_watcher.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/check_md_drift.sh"
