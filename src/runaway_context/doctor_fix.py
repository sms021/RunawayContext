"""Interactive fix flow for ``runaway doctor --fix*`` subcommands.

Every fix:

1. Computes a unified diff or a preview of the change before any write.
2. Writes the original file to ``~/.runaway/doctor-backups/<UTC-timestamp>/``
   along with a ``manifest.json`` describing what was changed.
3. Applies the change.
4. Prints the manifest path and the one-line revert command.

A single ``runaway doctor --revert <timestamp>`` reverses every fix done in
that batch by restoring the original file contents from the backup dir.

The user is prompted before each write (unless ``--yes`` was passed) with the
remediation message and the diff. Every fix can be declined individually.

Refuses:
    Writing without a successful prior backup. If the backup directory can't
    be created or written to, the fix is skipped and the error reported.
"""

from __future__ import annotations

import datetime
import difflib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


_BACKUP_ROOT = Path.home() / ".runaway" / "doctor-backups"


# ---------------------------------------------------------------------------
# Backup / manifest plumbing
# ---------------------------------------------------------------------------


@dataclass
class FixManifest:
    """Manifest recorded for one ``runaway doctor --fix*`` batch."""

    timestamp: str
    backup_dir: Path
    fixes: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, target: str, backup: str, notes: str = "") -> None:
        self.fixes.append({
            "kind": kind, "target": target, "backup": backup, "notes": notes,
        })

    def write(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "timestamp": self.timestamp,
                    "backup_dir": str(self.backup_dir),
                    "fixes": self.fixes,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def _start_batch() -> FixManifest:
    """Create a timestamped backup dir for the current fix batch."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = _BACKUP_ROOT / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    return FixManifest(timestamp=ts, backup_dir=backup_dir)


def _stash_original(manifest: FixManifest, path: Path) -> Path:
    """Copy *path* into the manifest's backup dir, preserving relative shape."""
    rel = str(path).lstrip("/").replace(os.sep, "_")
    dest = manifest.backup_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, dest)
    else:
        dest.write_text("", encoding="utf-8")  # placeholder
    return dest


# ---------------------------------------------------------------------------
# User interaction
# ---------------------------------------------------------------------------


def _confirm(prompt: str, *, default_no: bool = True, assume_yes: bool = False) -> bool:
    """Prompt the user for y/n; non-interactive shells default to NO."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    suffix = "[y/N]" if default_no else "[Y/n]"
    try:
        ans = input(f"{prompt} {suffix} ").strip().lower()
    except EOFError:
        return False
    if not ans:
        return not default_no
    return ans in ("y", "yes")


def _print_diff(label: str, before: str, after: str) -> None:
    """Print a unified diff of before/after to stdout."""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label} (current)",
        tofile=f"{label} (proposed)",
        n=3,
    )
    sys.stdout.writelines(diff)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Fix: Constitution rewrite
# ---------------------------------------------------------------------------


def _rewrite_constitution_block(text: str) -> Optional[str]:
    """Return rewritten text, or None if no changes needed.

    Targets the v2-era "Session Memory" section that referenced
    ``python3 _knowledge/sessions.py`` and replaces it with a v3-aware block.
    Conservative: only the lines that match are rewritten; surrounding content
    is left intact.
    """
    if "python3 _knowledge/sessions.py" not in text \
            and "python3 ~/_knowledge/sessions.py" not in text:
        return None

    new_lines = []
    in_legacy_block = False
    skipped_any = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("## Session Memory"):
            in_legacy_block = True
            new_lines.append(
                "## Session Memory — RunawayContext v3\n"
                "- **CLI**: `runaway brief <slug>` / `runaway search \"<query>\"` "
                "/ `runaway sessions ingest`\n"
                "- **MCP**: prefer the `get_brief` / `search_lessons` / "
                "`search_chunks` MCP tools when in Claude Code\n"
                "- **Auto-capture**: Stop hook (`~/_knowledge/bin/capture_session.sh`) "
                "and cron watcher (`bin/watch_sessions.sh`) handle logging — "
                "no manual save command needed\n"
            )
            skipped_any = True
            continue
        if in_legacy_block:
            # End the legacy block at the next top-level section.
            if stripped.startswith("## ") or stripped.startswith("# "):
                in_legacy_block = False
                new_lines.append(line)
                continue
            # Drop the old commands; carry through anything that doesn't
            # mention sessions.py.
            if "sessions.py" in line:
                continue
            new_lines.append(line)
            continue
        new_lines.append(line)
    if not skipped_any:
        return None
    return "".join(new_lines)


def fix_constitution(*, assume_yes: bool = False) -> Optional[Path]:
    """Rewrite ``~/CLAUDE.md`` Session Memory block to v3.

    Returns:
        Path to the new manifest file on success, ``None`` if no changes
        were needed or the user declined.
    """
    path = Path.home() / "CLAUDE.md"
    if not path.exists():
        print("no ~/CLAUDE.md to fix")
        return None
    text = path.read_text(encoding="utf-8")
    new_text = _rewrite_constitution_block(text)
    if new_text is None or new_text == text:
        print("~/CLAUDE.md has no stale v2 references")
        return None

    print("Proposed change to ~/CLAUDE.md:")
    _print_diff("CLAUDE.md", text, new_text)
    print(
        "This rewrite is REVERSIBLE — the original is backed up and "
        "`runaway doctor --revert <timestamp>` restores it.\n"
    )
    if not _confirm("Apply this rewrite?", default_no=True, assume_yes=assume_yes):
        print("declined.")
        return None

    manifest = _start_batch()
    backup = _stash_original(manifest, path)
    path.write_text(new_text, encoding="utf-8")
    manifest.add("constitution", str(path), str(backup),
                 notes="Session Memory block rewritten for v3 CLI/MCP")
    manifest.write()
    print(f"applied. backup at {manifest.backup_dir}")
    print(f"revert: runaway doctor --revert {manifest.timestamp}")
    return manifest.backup_dir / "manifest.json"


# ---------------------------------------------------------------------------
# Fix: MEMORY.md rewrite
# ---------------------------------------------------------------------------


def _rewrite_memory_md(text: str) -> Optional[str]:
    """Strip the trailing 'How to fetch detail' block that names sessions.py."""
    if "sessions.py" not in text:
        return None
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    in_fetch_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## How to fetch detail") or stripped.startswith("## How to fetch"):
            in_fetch_block = True
            out.append(
                "## How to fetch detail (v3)\n"
                "- `runaway brief <slug>` — project brief\n"
                "- `runaway search \"<query>\"` — search lessons + chunks\n"
                "- MCP tools (preferred in Claude Code): `get_brief`, "
                "`search_lessons`, `search_chunks`\n"
            )
            continue
        if in_fetch_block:
            if stripped.startswith("## ") or stripped.startswith("# "):
                in_fetch_block = False
                out.append(line)
                continue
            if "sessions.py" in line:
                continue
            out.append(line)
            continue
        if "sessions.py" in line:
            continue  # stray reference outside the fetch block
        out.append(line)
    new_text = "".join(out)
    return new_text if new_text != text else None


def fix_memory_md(*, install_dir: Optional[Path] = None, assume_yes: bool = False) -> Optional[Path]:
    """Rewrite stale MEMORY.md files to v3-aware fetch commands."""
    home = Path.home()
    candidates: List[Path] = []
    proj_root = home / ".claude" / "projects"
    if proj_root.exists():
        candidates.extend(proj_root.rglob("memory/MEMORY.md"))
    candidates = sorted({c for c in candidates if c.exists()})
    targets = []
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="replace")
        new_text = _rewrite_memory_md(text)
        if new_text is not None:
            targets.append((path, text, new_text))
    if not targets:
        print("no MEMORY.md files reference the pre-v3 sessions.py CLI")
        return None

    for path, before, after in targets:
        print(f"Proposed change to {path}:")
        _print_diff(str(path), before, after)
    print(
        f"{len(targets)} file(s) will be rewritten. Backups go to "
        "~/.runaway/doctor-backups/<ts>/ and revert is one command.\n"
    )
    if not _confirm("Apply all rewrites?", default_no=True, assume_yes=assume_yes):
        print("declined.")
        return None

    manifest = _start_batch()
    for path, _before, after in targets:
        backup = _stash_original(manifest, path)
        path.write_text(after, encoding="utf-8")
        manifest.add("memory_md", str(path), str(backup),
                     notes="trailing fetch-detail block rewritten")
    manifest.write()
    print(f"applied. backup at {manifest.backup_dir}")
    print(f"revert: runaway doctor --revert {manifest.timestamp}")
    return manifest.backup_dir / "manifest.json"


# ---------------------------------------------------------------------------
# Fix: MCP wiring
# ---------------------------------------------------------------------------


def fix_mcp(*, runaway_cli: Optional[str] = None, assume_yes: bool = False) -> Optional[Path]:
    """Merge a runaway-context entry into ``~/.claude/mcp.json``."""
    import shutil as _sh

    path = Path.home() / ".claude" / "mcp.json"
    if runaway_cli is None:
        runaway_cli = _sh.which("runaway") or "runaway"

    data: Dict[str, Any] = {}
    before = ""
    if path.exists():
        before = path.read_text(encoding="utf-8")
        try:
            data = json.loads(before)
        except json.JSONDecodeError:
            print(f"{path} is malformed JSON — refusing to merge automatically.")
            return None

    servers = data.setdefault("mcpServers", {})
    if "runaway-context" in servers:
        print(f"runaway-context already wired in {path}")
        return None
    servers["runaway-context"] = {"command": runaway_cli, "args": ["mcp", "serve"]}
    after = json.dumps(data, indent=2) + "\n"

    print(f"Proposed change to {path}:")
    _print_diff(str(path), before, after)
    if not _confirm("Apply this change?", default_no=True, assume_yes=assume_yes):
        print("declined.")
        return None

    manifest = _start_batch()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _stash_original(manifest, path)
    path.write_text(after, encoding="utf-8")
    manifest.add("mcp.json", str(path), str(backup), notes="added runaway-context server")
    manifest.write()
    print(f"applied. backup at {manifest.backup_dir}")
    print(f"revert: runaway doctor --revert {manifest.timestamp}")
    return manifest.backup_dir / "manifest.json"


# ---------------------------------------------------------------------------
# Fix: Stop hook for session capture
# ---------------------------------------------------------------------------


def fix_capture_hook(
    *, install_dir: Optional[Path] = None, assume_yes: bool = False,
    capture_script: Optional[Path] = None,
) -> Optional[Path]:
    """Wire ``capture_session.sh`` into ``~/.claude/settings.json`` Stop hook."""
    path = Path.home() / ".claude" / "settings.json"
    data: Dict[str, Any] = {}
    before = ""
    if path.exists():
        before = path.read_text(encoding="utf-8")
        try:
            data = json.loads(before)
        except json.JSONDecodeError:
            print(f"{path} is malformed JSON — refusing to merge automatically.")
            return None

    if capture_script is None:
        # Default: ship-with-repo path. Adopters can override via flag.
        capture_script = Path(__file__).parent.parent.parent / "bin" / "capture_session.sh"
    capture_script = capture_script.resolve()

    hooks = data.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    # Idempotent: don't add if already present.
    for group in stop:
        for h in group.get("hooks", []) or []:
            if str(capture_script) in (h.get("command") or ""):
                print(f"capture hook already wired in {path}")
                return None
    stop.append({
        "hooks": [
            {"type": "command", "command": str(capture_script)},
        ],
    })
    after = json.dumps(data, indent=2) + "\n"

    print(f"Proposed change to {path}:")
    _print_diff(str(path), before, after)
    if not _confirm("Apply this change?", default_no=True, assume_yes=assume_yes):
        print("declined.")
        return None

    manifest = _start_batch()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _stash_original(manifest, path)
    path.write_text(after, encoding="utf-8")
    manifest.add(
        "settings.json", str(path), str(backup),
        notes=f"appended Stop hook for {capture_script}",
    )
    manifest.write()
    print(f"applied. backup at {manifest.backup_dir}")
    print(f"revert: runaway doctor --revert {manifest.timestamp}")
    return manifest.backup_dir / "manifest.json"


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def revert(timestamp: str) -> int:
    """Restore every file in the named backup batch.

    Returns:
        Number of files restored. 0 when the timestamp does not exist.
    """
    batch_dir = _BACKUP_ROOT / timestamp
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}")
        return 0
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"manifest at {manifest_path} is malformed")
        return 0
    restored = 0
    for fix in m.get("fixes", []):
        target = Path(fix.get("target", ""))
        backup = Path(fix.get("backup", ""))
        if not target or not backup.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        restored += 1
        print(f"restored {target}")
    print(f"revert complete — {restored} file(s) restored from {batch_dir}")
    return restored


def list_batches() -> List[str]:
    """Return all available revert timestamps, newest first."""
    if not _BACKUP_ROOT.exists():
        return []
    return sorted(
        (p.name for p in _BACKUP_ROOT.iterdir() if (p / "manifest.json").exists()),
        reverse=True,
    )
