#!/usr/bin/env bash
# RunawayContext v2 — multi-user rollout helper.
#
# In a shared environment (team server, family computer, multi-Linux-user box),
# every Claude Code / AI-tool user needs the same three things:
#   1. Stop hook in their settings.json so drift-detection fires at session end.
#   2. A starter MEMORY.md pointing at the new-system commands (created if absent;
#      preserved if they already have one).
#   3. (Optional) a per-user script alias / PATH entry so they can call the
#      RunawayContext CLI without typing the full path.
#
# What this script does NOT do:
#   - Install RunawayContext itself (clone the repo first).
#   - Fork the KS — all users share one knowledge.db / sessions.db at $RC_KS_DIR.
#   - Migrate v1 → v2 — that's a separate concern (see lib/migrate_v1_to_v2.py).
#
# Run with sudo if you're touching other users' home directories.
#
# Usage:
#   ./bin/setup_user_protections.sh [--apply] [--all | --user NAME]
#       --apply        Actually write changes (default is dry-run preview)
#       --all          Discover and process all users with ~/.claude/ dirs
#       --user NAME    Process /home/NAME/.claude (or /Users/NAME/.claude on Mac)
#
# Cross-platform: detects /Users on macOS, /home on Linux.

set -uo pipefail

# Resolve install paths
RC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DRIFT_SCRIPT="$RC_DIR/bin/check_md_drift.sh"

# Detect home root by OS
case "$(uname -s)" in
    Darwin*) HOME_ROOT=/Users ;;
    Linux*)  HOME_ROOT=/home ;;
    *)       echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac

DRY_RUN=1
TARGETS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --all)
            for d in "$HOME_ROOT"/*/.claude; do
                [ -d "$d" ] || continue
                user=$(basename "$(dirname "$d")")
                TARGETS+=("$user")
            done
            shift ;;
        --user) TARGETS+=("$2"); shift 2 ;;
        --help|-h)
            grep '^#' "$0" | head -25 | sed 's/^# \?//'
            exit 0 ;;
        *) shift ;;
    esac
done

if [ ${#TARGETS[@]} -eq 0 ]; then
    grep '^#' "$0" | head -25 | sed 's/^# \?//'
    exit 1
fi

apply_to() {
    local user="$1"
    local home_dir="$HOME_ROOT/$user"

    if [ ! -d "$home_dir/.claude" ]; then
        echo "=== $user — no .claude dir, skipping ==="
        return
    fi

    local settings_file="$home_dir/.claude/settings.json"
    local memory_dir="$home_dir/.claude/projects"
    local memory_file=""

    # Find or seed the memory file. Claude Code's encoded-path scheme means
    # the projects subdir is project-specific; we drop our seed at .claude/
    # MEMORY.md as a generic location and let the user move it if needed.
    memory_file="$home_dir/.claude/MEMORY.md"

    local today
    today=$(date +%Y%m%d)

    echo
    echo "=== $user (HOME=$home_dir) ==="

    # Helper: backup a file in the SAME directory before we touch it.
    # No-op in dry-run mode; no-op if file doesn't exist.
    backup_file() {
        local f="$1"
        [ "$DRY_RUN" -eq 1 ] && return 0
        [ -f "$f" ] || return 0
        local bak="${f}.pre-rc-v2.${today}.bak"
        if [ -e "$bak" ]; then
            # Already backed up today; leave it alone (preserve first state of the day)
            return 0
        fi
        cp -p "$f" "$bak" 2>/dev/null || {
            echo "  ⚠ could not backup $f (permission?)"
            return 1
        }
        echo "  ↳ backup: $bak"
    }

    # 1. Settings.json — merge Stop hook (preserve existing keys)
    # Tolerate missing file or unreadable file by defaulting to {}.
    local existing="{}"
    if [ -r "$settings_file" ]; then
        local raw
        raw=$(cat "$settings_file" 2>/dev/null || true)
        # If the file exists but is unreadable or empty, fall back to {}
        if [ -n "$raw" ]; then
            existing="$raw"
        fi
    fi
    local new
    new=$(printf '%s' "$existing" | python3 -c "
import json, sys
raw = sys.stdin.read().strip()
data = json.loads(raw) if raw else {}
hooks = data.setdefault('hooks', {})
stops = hooks.setdefault('Stop', [])
already = any(
    any(h.get('command', '').endswith('check_md_drift.sh') for h in s.get('hooks', []))
    for s in stops
)
if not already:
    stops.append({'hooks': [{'type': 'command', 'command': '$DRIFT_SCRIPT'}]})
print(json.dumps(data, indent=2))
" 2>/dev/null) || {
        echo "  ✗ failed to merge settings.json (permission denied or invalid JSON?)"
        return
    }
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [dry-run] would backup + add Stop hook to $settings_file"
    else
        backup_file "$settings_file"
        printf '%s\n' "$new" > "$settings_file"
        chown "$user" "$settings_file"
        chmod 640 "$settings_file"
        echo "  ✓ Stop hook in $settings_file"
    fi

    # 2. MEMORY.md — seed if missing; otherwise prepend pointer block
    # (preserves existing notes underneath). Either way, backup the existing
    # file first if there is one.
    local pointer_block
    pointer_block=$(cat <<EOF
# Auto Memory

> Knowledge architecture (read this first):
> 1. **First action when entering any project:** \`python3 $RC_DIR/lib/ll_brief.py --brief <project_slug>\` — returns the project manifest. Don't grep, don't read changelogs.
> 2. **Every project CLAUDE.md is AUTO-GENERATED** from knowledge.db. Do NOT hand-edit. Edits between \`<!-- PRESERVE_START -->\` and \`<!-- PRESERVE_END -->\` survive regen; everything else gets wiped.
> 3. **Logging knowledge** (\`--ll-projects\` / \`--project\` REQUIRED):
>    - Burned-us incident → \`python3 $RC_DIR/lib/ll_brief.py --log-lesson --ll-projects <slug> --ll-title "..." ...\`
>    - Discipline / data fact → \`python3 $RC_DIR/lib/propose_knowledge.py --project <slug> --topic <slug> --title "..." --body "..."\`
> 4. **This MEMORY.md** = behavioral gotchas about YOUR OWN performance. Aim ≤30 lines. Anything bigger goes in DB.

EOF
)
    if [ ! -s "$memory_file" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "  [dry-run] would create $memory_file with new-system pointer"
        else
            printf '%s\n' "$pointer_block" > "$memory_file"
            chown "$user" "$memory_file"
            chmod 640 "$memory_file"
            echo "  ✓ Wrote pointer to $memory_file (new file)"
        fi
    else
        # Existing file — only modify if pointer not already present
        if grep -q "knowledge.py --brief\|ll_brief.py --brief" "$memory_file" 2>/dev/null; then
            echo "  (existing MEMORY.md already has pointer — left alone)"
        else
            if [ "$DRY_RUN" -eq 1 ]; then
                echo "  [dry-run] would backup + prepend pointer to existing $memory_file ($(wc -l < "$memory_file" | tr -d ' ') lines)"
            else
                backup_file "$memory_file"
                local existing_content
                existing_content=$(cat "$memory_file")
                {
                    printf '%s\n' "$pointer_block"
                    printf '%s\n' "## Existing notes (preserved from prior MEMORY.md)"
                    # Strip a leading H1 from the existing file to avoid double H1
                    printf '%s\n' "$existing_content" | awk 'BEGIN{skipped=0} /^# / && skipped==0 {skipped=1; next} {print}'
                } > "$memory_file"
                chown "$user" "$memory_file"
                chmod 640 "$memory_file"
                echo "  ✓ Prepended pointer; existing notes preserved beneath"
            fi
        fi
    fi
}

for target in "${TARGETS[@]}"; do
    apply_to "$target"
done

echo
[ "$DRY_RUN" -eq 1 ] && echo "DRY RUN — re-run with --apply to commit changes."
