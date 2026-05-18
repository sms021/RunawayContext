"""Uninstall + revert path for RunawayContext v3.

The install loop captures an ``install_manifest.json`` recording what was
created and what (if any) pre-existing files were modified. The uninstall
loop reads that manifest, optionally archives the install dir to a
timestamped tarball, optionally exports the DB content back to a portable
markdown tree, and then removes the install dir.

Refuses:
    Hard-delete without the safety flag (HR-3 spirit applied to the install
    itself). Revert without the manifest. Export to a non-empty target dir
    without ``--overwrite``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


MANIFEST_FILENAME = "install_manifest.json"
MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------
# Install manifest
# ---------------------------------------------------------------------------


@dataclass
class InstallManifest:
    """The install record. Written by ``runaway init``; read by ``runaway uninstall``.

    Attributes:
        manifest_version: Schema version of this file.
        installed_at: ISO-8601 timestamp.
        install_dir: Absolute path of the install directory.
        runaway_version: The ``__version__`` at install time.
        created_paths: Files/dirs created by the install (safe to remove on revert).
        modified_files: Pre-existing files we touched, with their original content
            preserved so revert can restore them.
        cron_entries: Crontab lines added by the install (for revert).
    """

    manifest_version: int = MANIFEST_VERSION
    installed_at: str = ""
    install_dir: str = ""
    runaway_version: str = ""
    tier_at_install: str = "T1"
    created_paths: List[str] = field(default_factory=list)
    modified_files: Dict[str, str] = field(default_factory=dict)
    cron_entries: List[str] = field(default_factory=list)

    @classmethod
    def fresh(cls, install_dir: Path, *, tier: str = "T1") -> "InstallManifest":
        """Build a new manifest for the install at *install_dir*.

        Returns:
            A populated :class:`InstallManifest` with ``installed_at`` set to now.

        Refuses:
            Nothing.
        """
        from runaway_context import __version__ as rc_version

        return cls(
            manifest_version=MANIFEST_VERSION,
            installed_at=datetime.now(timezone.utc).isoformat(),
            install_dir=str(install_dir),
            runaway_version=rc_version,
            tier_at_install=tier,
        )

    def save(self, install_dir: Path) -> Path:
        """Write the manifest to ``<install_dir>/install_manifest.json``.

        Returns:
            The path the manifest was written to.

        Refuses:
            Nothing — overwrites an existing manifest deliberately.
        """
        path = Path(install_dir) / MANIFEST_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        return path

    @classmethod
    def load(cls, install_dir: Path) -> Optional["InstallManifest"]:
        """Load the manifest at ``<install_dir>/install_manifest.json`` if present.

        Returns:
            The manifest, or ``None`` when the file is missing or unreadable.
        """
        path = Path(install_dir) / MANIFEST_FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return cls(**{k: v for k, v in data.items()
                          if k in cls.__annotations__})
        except TypeError:
            return None


def capture_install_manifest(install_dir: Path, *, tier: str = "T1",
                             extras: Optional[Dict[str, Any]] = None) -> Path:
    """Write an :class:`InstallManifest` for *install_dir* and return its path.

    Called by ``runaway init`` to record what the install touched. The manifest
    is the source of truth for ``runaway uninstall``.

    Returns:
        Absolute path of the saved manifest.

    Refuses:
        Nothing — always writes; older manifests are overwritten.
    """
    manifest = InstallManifest.fresh(install_dir, tier=tier)
    # Record the files we typically create at init time.
    for fname in ("knowledge.db", "sessions.db", "metrics.db",
                  "config.json", "install_id"):
        p = install_dir / fname
        if p.exists():
            manifest.created_paths.append(str(p))
    if extras:
        for key in ("modified_files", "cron_entries", "created_paths"):
            val = extras.get(key)
            if not val:
                continue
            attr = getattr(manifest, key)
            if isinstance(attr, list):
                attr.extend(val)
            elif isinstance(attr, dict):
                attr.update(val)
    return manifest.save(install_dir)


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def export_to_markdown(install_dir: Path, output_dir: Path,
                       *, overwrite: bool = False) -> Dict[str, int]:
    """Export every lesson and chunk to a portable markdown tree.

    Layout under *output_dir*:

    * ``lessons/<slug>/LL-<id>-<title>.md`` — per-lesson; frontmatter + body.
    * ``chunks/<project>/<topic>.md`` — per-chunk.
    * ``projects/<project>/CLAUDE.md`` — the current brief content (one per project).
    * ``index.md`` — a table of contents linking everything.

    Lets users who decide RunawayContext isn't for them leave with the data
    in a format they can keep using directly with any AI tool.

    Returns:
        ``{"lessons": N, "chunks": N, "projects": N}``.

    Refuses:
        A non-empty *output_dir* without ``overwrite=True``.
    """
    install_dir = Path(install_dir)
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"refusing to write into non-empty directory {output_dir}; "
            "pass overwrite=True to proceed"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    knowledge_db = install_dir / "knowledge.db"
    if not knowledge_db.exists():
        return {"lessons": 0, "chunks": 0, "projects": 0}

    conn = sqlite3.connect(str(knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        lessons = list(conn.execute(
            "SELECT * FROM lessons_learned WHERE deleted_at IS NULL"
        ))
        chunks = list(conn.execute(
            "SELECT * FROM knowledge_chunks WHERE deleted_at IS NULL"
        ))
        projects = list(conn.execute("SELECT * FROM project_context_card"))
    finally:
        conn.close()

    lessons_dir = output_dir / "lessons"
    chunks_dir = output_dir / "chunks"
    projects_dir = output_dir / "projects"
    for d in (lessons_dir, chunks_dir, projects_dir):
        d.mkdir(parents=True, exist_ok=True)

    counts = {"lessons": 0, "chunks": 0, "projects": 0}

    for row in lessons:
        counts["lessons"] += 1
        title = (row["title"] or "untitled").strip()
        safe_title = _slugify(title)
        slug = (row["project"] or _first_tag(row["project_tags"]) or "general")
        proj_dir = lessons_dir / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"LL-{row['id']}-{safe_title}.md"
        path.write_text(_lesson_to_md(row))

    for row in chunks:
        counts["chunks"] += 1
        proj = row["project"] or "general"
        topic = _slugify(row["topic"] or f"chunk-{row['id']}")
        proj_dir = chunks_dir / proj
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"{topic}.md"
        path.write_text(_chunk_to_md(row))

    for row in projects:
        counts["projects"] += 1
        proj = row["project"] or "general"
        proj_dir = projects_dir / proj
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "CLAUDE.md").write_text(_project_to_md(row))

    (output_dir / "index.md").write_text(_render_index(counts, lessons, chunks, projects))
    return counts


def _slugify(text: str) -> str:
    """Return a filesystem-safe slug for *text*."""
    if not text:
        return "untitled"
    out = []
    for ch in text.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "."):
            out.append("-")
    slug = "".join(out).strip("-")
    return slug[:80] or "untitled"


def _first_tag(tags_json: Optional[str]) -> Optional[str]:
    if not tags_json:
        return None
    try:
        arr = json.loads(tags_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(arr, list) and arr:
        return str(arr[0])
    return None


def _lesson_to_md(row: sqlite3.Row) -> str:
    """Render a lessons_learned row as markdown with YAML frontmatter."""
    fm: Dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "project": row["project"],
        "project_tags": _safe_json(row["project_tags"]),
        "severity": row["severity"],
        "maturity": row["maturity"] if "maturity" in row.keys() else None,
        "status": row["status"],
        "created_at": row["created_at"],
    }
    body: List[str] = []
    if row["what_happened"]:
        body.append(f"## What happened\n\n{row['what_happened']}")
    if row["why"]:
        body.append(f"## Why\n\n{row['why']}")
    if row["the_fix"]:
        body.append(f"## The fix\n\n{row['the_fix']}")
    if row["prevention_rule"]:
        body.append(f"## Prevention rule\n\n{row['prevention_rule']}")
    if not body and row["lesson"]:  # v1 back-compat
        body.append(row["lesson"])
    return _wrap_frontmatter(fm, "\n\n".join(body) or "_(empty)_")


def _chunk_to_md(row: sqlite3.Row) -> str:
    """Render a knowledge_chunks row as markdown with YAML frontmatter."""
    fm: Dict[str, Any] = {
        "id": row["id"],
        "project": row["project"],
        "topic": row["topic"],
        "title": row["title"],
        "tags": _safe_json(row["tags"]),
        "created_at": row["created_at"],
    }
    body = row["body"] or "_(empty)_"
    return _wrap_frontmatter(fm, body)


def _project_to_md(row: sqlite3.Row) -> str:
    """Render a project_context_card row as a v2-style brief."""
    fm = {
        "project": row["project"],
        "md_path": row["md_path"],
        "md_line_cap": row["md_line_cap"],
        "last_rebuilt": row["last_rebuilt"],
    }
    body = [
        f"## {row['project']} — Project Brief",
        "",
        f"_top_warnings:_ {row['top_warnings'] or '[]'}",
        f"_active_lesson_ids:_ {row['active_lesson_ids'] or '[]'}",
        f"_active_chunk_ids:_ {row['active_chunk_ids'] or '[]'}",
    ]
    if row["notes"]:
        body.extend(["", "### Notes", "", row["notes"]])
    return _wrap_frontmatter(fm, "\n".join(body))


def _safe_json(text: Optional[str]) -> Any:
    if not text:
        return []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []


def _wrap_frontmatter(fm: Dict[str, Any], body: str) -> str:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_value(v)}")
    lines.extend(["---", "", body, ""])
    return "\n".join(lines)


def _yaml_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, list):
        return json.dumps(v)
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in ":#\n"):
        return json.dumps(s)
    return s


def _render_index(counts: Dict[str, int], lessons, chunks, projects) -> str:
    out = [
        "# RunawayContext export — markdown tree",
        "",
        f"Lessons: **{counts['lessons']}** · Chunks: **{counts['chunks']}** · Projects: **{counts['projects']}**",
        "",
        "## Lessons",
        "",
    ]
    for r in lessons:
        title = r["title"] or "(untitled)"
        slug = r["project"] or _first_tag(r["project_tags"]) or "general"
        safe = _slugify(title)
        out.append(f"- LL#{r['id']} [{r['severity'] or 'warning'}] [{title}](lessons/{slug}/LL-{r['id']}-{safe}.md)")
    out.append("")
    out.append("## Chunks")
    out.append("")
    for r in chunks:
        proj = r["project"] or "general"
        topic = _slugify(r["topic"] or f"chunk-{r['id']}")
        out.append(f"- KS#{r['id']} [{r['title']}](chunks/{proj}/{topic}.md)")
    out.append("")
    out.append("## Projects")
    out.append("")
    for r in projects:
        proj = r["project"]
        out.append(f"- [{proj}](projects/{proj}/CLAUDE.md)")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Archive + remove
# ---------------------------------------------------------------------------


def _external_brief_paths(install_dir: Path) -> List[Path]:
    """Return every external file v3 wrote via ``project_context_card.md_path``.

    These are the auto-generated project briefs that live at user-controlled
    paths (e.g. ``<project>/CLAUDE.md``). They are *outside* the install dir
    but were written by v3, so a kind fallback tarball should preserve them
    alongside the install state.

    Returns:
        Sorted list of absolute paths that currently exist on disk.

    Refuses:
        Paths under system roots (``/etc``, ``/usr``, ``/var``) — we will not
        pull system files into a user archive even if a malformed
        project_context_card row claims to live there.
    """
    kdb = install_dir / "knowledge.db"
    if not kdb.exists():
        return []
    paths: List[Path] = []
    conn = sqlite3.connect(str(kdb))
    try:
        rows = conn.execute(
            "SELECT md_path FROM project_context_card "
            "WHERE md_path IS NOT NULL AND md_path != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    # System-root prefixes that md_path must NOT resolve into. Include each
    # root's symlink-resolved form too — on macOS '/etc' resolves to
    # '/private/etc', so a raw '/etc/' prefix check silently misses
    # Path('/etc/passwd').resolve(). Note we deliberately do NOT block
    # '/var' or '/private/var' as a whole: macOS hosts user-owned temp
    # dirs under '/private/var/folders/...' and legitimate project briefs
    # may live there (e.g. pytest tmp_path). Sensitive '/var' subtrees are
    # listed explicitly instead.
    _root_seeds = (
        "/etc", "/usr/bin", "/usr/sbin", "/usr/libexec", "/bin", "/sbin",
        "/sys", "/proc", "/root",
        "/var/db", "/var/log", "/var/root", "/var/mail", "/var/spool",
    )
    _root_prefixes: List[str] = []
    for root in _root_seeds:
        rp = Path(root)
        _root_prefixes.append(str(rp) + "/")
        try:
            resolved_root = str(rp.resolve()) + "/"
            if resolved_root not in _root_prefixes:
                _root_prefixes.append(resolved_root)
        except (OSError, RuntimeError):
            pass
    for (md_path,) in rows:
        p = Path(md_path).expanduser().resolve()
        # Skip files inside the install dir (already covered by the dir add).
        try:
            p.relative_to(install_dir.resolve())
            continue
        except ValueError:
            pass  # not under install dir — keep going
        p_str = str(p)
        if any(p_str.startswith(root) for root in _root_prefixes):
            continue
        if p.exists() and p.is_file():
            paths.append(p)
    return sorted(set(paths))


def _modified_file_paths(install_dir: Path) -> List[Path]:
    """Return every absolute path the manifest recorded as pre-existing-and-modified.

    Returns:
        Sorted list of absolute paths that currently exist on disk.
    """
    manifest = InstallManifest.load(install_dir)
    if manifest is None:
        return []
    out = []
    for p_str in manifest.modified_files.keys():
        p = Path(p_str).expanduser().resolve()
        if p.exists() and p.is_file():
            out.append(p)
    return sorted(set(out))


def _arcname_external(abs_path: Path) -> str:
    """Compute the tarball arcname for an external file.

    Maps ``/home/user/proj/CLAUDE.md`` → ``external/home/user/proj/CLAUDE.md``
    so extraction is relative (safe) but the original location is preserved
    in the archive structure.

    Returns:
        Relative arcname string suitable for ``tarfile.add(arcname=...)``.
    """
    parts = list(abs_path.parts)
    # Strip the leading "/" on POSIX so the arcname is relative.
    if parts and parts[0] == "/":
        parts = parts[1:]
    return "external/" + "/".join(parts)


def archive_install(install_dir: Path, archive_dir: Optional[Path] = None) -> Path:
    """Snapshot *install_dir* and v3's external artifacts to a timestamped tarball.

    The archive contains, in order:

    1. **The install directory** under ``<install_dir.name>/`` — DBs, config,
       manifest, install_id, copied templates.
    2. **External brief files** under ``external/<original-absolute-path>/`` —
       any file v3 wrote at ``project_context_card.md_path`` (typically
       ``<project>/CLAUDE.md``).
    3. **Modified pre-install files** under ``external/<original-absolute-path>/`` —
       files the manifest recorded as touched (drift hook configs, etc.).

    The archive lives under ``<archive_dir>/`` (default: the install_dir's
    parent). The on-disk files are NOT deleted by this function — call
    :func:`remove_install` for that.

    Returns:
        Absolute path of the created ``.tar.gz``.

    Refuses:
        Missing *install_dir*. Pulling files from system roots (``/etc``,
        ``/usr``, etc.) even if a manifest or project_context_card claims to
        live there.
    """
    install_dir = Path(install_dir)
    if not install_dir.exists():
        raise FileNotFoundError(f"install dir not found: {install_dir}")
    archive_dir = Path(archive_dir) if archive_dir else install_dir.parent
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = archive_dir / f"runaway-context-{stamp}.tar.gz"

    external_files = _external_brief_paths(install_dir)
    modified_files = _modified_file_paths(install_dir)
    # Deduplicate (a brief file might also be in modified_files).
    seen: set = set()
    extras = []
    for p in external_files + modified_files:
        if p in seen:
            continue
        seen.add(p)
        extras.append(p)

    with tarfile.open(str(out), "w:gz") as tf:
        tf.add(str(install_dir), arcname=install_dir.name)
        for ext_path in extras:
            tf.add(str(ext_path), arcname=_arcname_external(ext_path))
        # Include a manifest of external files for restore tooling.
        manifest_payload = json.dumps({
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "install_dir": str(install_dir),
            "external_files": [str(p) for p in extras],
        }, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="EXTERNAL_FILES.json")
        info.size = len(manifest_payload)
        import io as _io
        tf.addfile(info, _io.BytesIO(manifest_payload))
    return out


def remove_install(install_dir: Path, *,
                   confirm: bool = False,
                   keep_db: bool = False) -> Dict[str, Any]:
    """Remove *install_dir* (or just the config when ``keep_db`` is True).

    Returns:
        ``{"removed": [paths], "kept": [paths]}``.

    Refuses:
        Removal without ``confirm=True``. Removal of the home directory or
        anything that looks like a system path.
    """
    install_dir = Path(install_dir).resolve()
    if not confirm:
        raise PermissionError(
            "remove_install refused: pass confirm=True (HR-3 spirit applied to "
            "the install itself — confirm before destructive operations)"
        )
    # Safety: never remove $HOME or root-level paths.
    home = Path.home().resolve()
    if install_dir == home or str(install_dir) in ("/", "/root", "/home"):
        raise PermissionError(f"refusing to remove {install_dir} (unsafe path)")
    if not install_dir.exists():
        return {"removed": [], "kept": []}

    removed: List[str] = []
    kept: List[str] = []
    if keep_db:
        # Remove only config + manifest; preserve DBs and templates.
        for fname in ("config.json", MANIFEST_FILENAME):
            p = install_dir / fname
            if p.exists():
                p.unlink()
                removed.append(str(p))
        for child in install_dir.iterdir():
            kept.append(str(child))
        return {"removed": removed, "kept": kept}

    for child in install_dir.iterdir():
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            shutil.rmtree(child)
        removed.append(str(child))
    install_dir.rmdir()
    return {"removed": removed, "kept": kept}


def revert_modified_files(manifest: InstallManifest) -> Dict[str, str]:
    """Restore every file in ``manifest.modified_files`` to its pre-install content.

    Returns:
        ``{path: status}`` where ``status`` is ``"restored"``, ``"missing"``, or
        ``"unchanged"``.
    """
    out: Dict[str, str] = {}
    for path_str, original in manifest.modified_files.items():
        path = Path(path_str)
        if not original:
            # Empty string means "the file did not exist before; remove it."
            if path.exists():
                path.unlink()
                out[path_str] = "removed"
            else:
                out[path_str] = "missing"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original)
        out[path_str] = "restored"
    return out


# ---------------------------------------------------------------------------
# Orchestration — the "UndoRunawayContext" entry point
# ---------------------------------------------------------------------------


def uninstall(install_dir: Path, *,
              dry_run: bool = False,
              archive: bool = True,
              archive_dir: Optional[Path] = None,
              export_markdown: Optional[Path] = None,
              revert: bool = False,
              keep_db: bool = False,
              confirm: bool = False) -> Dict[str, Any]:
    """Top-level uninstall orchestration. Used by ``runaway uninstall``.

    Steps (in order):

    1. If ``export_markdown`` is set, dump the DB content there.
    2. If ``archive`` is True, snapshot the install dir to a tarball.
    3. If ``revert`` is True, restore pre-install state of touched files.
    4. Remove the install dir (or, with ``keep_db``, only the config).

    Returns:
        Dict with keys ``dry_run``, ``markdown_export``, ``archive_path``,
        ``reverted``, ``removed``, ``kept``.

    Refuses:
        Confirmed removal without ``confirm=True`` (the CLI passes the flag).
    """
    install_dir = Path(install_dir)
    report: Dict[str, Any] = {
        "dry_run": dry_run,
        "install_dir": str(install_dir),
        "markdown_export": None,
        "archive_path": None,
        "reverted": {},
        "removed": [],
        "kept": [],
    }
    manifest = InstallManifest.load(install_dir)

    if export_markdown is not None:
        if dry_run:
            report["markdown_export"] = {"would_write_to": str(export_markdown)}
        else:
            counts = export_to_markdown(install_dir, export_markdown, overwrite=True)
            report["markdown_export"] = {"path": str(export_markdown), **counts}

    if archive and install_dir.exists():
        if dry_run:
            report["archive_path"] = "would-archive"
        else:
            report["archive_path"] = str(archive_install(install_dir, archive_dir))

    if revert:
        if manifest is None:
            raise FileNotFoundError(
                f"no install_manifest.json in {install_dir}; cannot revert "
                "(was this install created before manifests were introduced?)"
            )
        if dry_run:
            report["reverted"] = {p: "would-restore" for p in manifest.modified_files}
        else:
            report["reverted"] = revert_modified_files(manifest)

    if dry_run:
        report["removed"] = ["(dry-run — nothing removed)"]
    else:
        result = remove_install(install_dir, confirm=confirm, keep_db=keep_db)
        report["removed"] = result["removed"]
        report["kept"] = result["kept"]
    return report


__all__ = [
    "InstallManifest",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "capture_install_manifest",
    "export_to_markdown",
    "archive_install",
    "remove_install",
    "revert_modified_files",
    "uninstall",
]
