"""Brief preview, snapshot, and rollback (E20).

The brief regenerator (built by a sibling module ``runaway_context.brief``)
produces a project's auto-managed ``CLAUDE.md`` from the knowledge store.
Two failure modes need first-class support:

1. **Preview before commit** — an AI proposing a regen wants to see the
   would-be content (and the line count) without touching the file.
2. **Rollback** — when a regen produces an output the human dislikes, the
   previous content must be recoverable. HR-3: every write is recoverable.

This module persists snapshots in the ``brief_snapshots`` table (DDL in
``schema/001_v3_additions.sql``) and records audit entries on rollback.

Refuses:
    Snapshotting a project whose md file does not exist (raises FileNotFoundError).
    Rolling back without an explicit ``actor`` argument (raises ValueError).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from runaway_context._db import connect, transaction
from runaway_context.config import Config


# ---------------------------------------------------------------- helpers

def _config_for(install_dir: Path) -> Config:
    """Load the Config that points at the install's knowledge.db."""
    return Config.load(install_dir)


def _md_path_for(install_dir: Path, project: str) -> Path:
    """Return the conventional CLAUDE.md path for ``project`` under ``install_dir``.

    The convention matches the regenerator (``brief.regenerate``):
    ``<install_dir>/briefs/<project>/CLAUDE.md``. The regenerator owns the
    real path; this helper is only used when no snapshot row has been written
    yet and we need a sensible default for the first snapshot.
    """
    return install_dir / "briefs" / project / "CLAUDE.md"


def _record_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    target_id: Optional[int],
    details: Dict[str, Any],
) -> None:
    """Append a hash-chained audit_log row (mirrors maturation.apply_transition).

    Uses ``runaway_context.audit.append`` when available (sibling agent's
    module); falls back to an inline chained-hash insert otherwise. Either
    path lands a row in audit_log with a verifiable previous/this hash pair.
    """
    audit_mod = None
    try:
        audit_mod = importlib.import_module("runaway_context.audit")
    except ImportError:
        audit_mod = None

    if audit_mod is not None and hasattr(audit_mod, "append"):
        audit_mod.append(
            conn,
            actor=actor,
            action=action,
            target_table="brief_snapshots",
            target_id=target_id,
            details=details,
        )
        return

    # Inline fallback — match the schema and hash discipline used elsewhere.
    payload = json.dumps(details, sort_keys=True, default=str)
    prev_row = conn.execute(
        "SELECT this_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    previous_hash = prev_row[0] if prev_row else None
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    h = hashlib.sha256(
        "|".join([
            previous_hash or "",
            actor,
            action,
            str(target_id) if target_id is not None else "",
            timestamp,
        ]).encode("utf-8")
    ).hexdigest()[:16]
    conn.execute(
        "INSERT INTO audit_log "
        "(occurred_at, actor, action, target_table, target_id, "
        " details, previous_hash, this_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (timestamp, actor, action, "brief_snapshots", target_id,
         payload, previous_hash, h),
    )


# ---------------------------------------------------------------- preview

def preview(install_dir: Path, project: str) -> Dict[str, Any]:
    """Return what ``brief.regenerate`` would produce without writing anything.

    The function imports ``runaway_context.brief`` lazily — if the regenerator
    has not been built yet (sibling agent in flight), this function returns
    a clear placeholder result rather than crashing the caller.

    Returns:
        A dict with at minimum the keys ``project``, ``content`` (string),
        ``line_count`` (int), ``would_violate_cap`` (bool), ``cap`` (int).
        When the regenerator is unavailable the dict also carries
        ``regenerator_available=False``.

    Refuses:
        Nothing — propagates whatever exception ``brief.regenerate(dry_run=True)``
        raises so the caller can surface it (HR-10: no silent failures).
    """
    install_dir = Path(install_dir)
    try:
        brief_mod = importlib.import_module("runaway_context.brief")
    except ImportError:
        existing = ""
        md_path = _md_path_for(install_dir, project)
        if md_path.exists():
            existing = md_path.read_text(encoding="utf-8")
        return {
            "project": project,
            "content": existing,
            "line_count": existing.count("\n") + (1 if existing else 0),
            "would_violate_cap": False,
            "cap": 150,
            "regenerator_available": False,
            "note": (
                "runaway_context.brief.regenerate not on disk yet — "
                "preview returned the current file content as a placeholder."
            ),
        }

    if not hasattr(brief_mod, "regenerate"):
        return {
            "project": project,
            "content": "",
            "line_count": 0,
            "would_violate_cap": False,
            "cap": 150,
            "regenerator_available": False,
            "note": "brief module loaded but exposes no regenerate() function",
        }

    result = brief_mod.regenerate(install_dir=install_dir,
                                  project=project,
                                  dry_run=True)
    if isinstance(result, dict):
        result.setdefault("project", project)
        result.setdefault("regenerator_available", True)
        return result

    # If the regenerator returned a string we wrap it into the canonical shape.
    if isinstance(result, str):
        return {
            "project": project,
            "content": result,
            "line_count": result.count("\n") + (1 if result else 0),
            "would_violate_cap": False,
            "cap": 150,
            "regenerator_available": True,
        }
    raise TypeError(
        f"brief.regenerate(dry_run=True) returned unexpected type "
        f"{type(result).__name__}"
    )


# ---------------------------------------------------------------- snapshot

def snapshot(install_dir: Path, project: str,
             note: Optional[str] = None,
             saved_by: Optional[str] = None) -> int:
    """Persist the current brief md content into ``brief_snapshots``.

    The function reads ``<install_dir>/briefs/<project>/CLAUDE.md`` (or
    whatever path the regenerator advertises via ``md_path_for``) and writes
    its full content into the snapshots table. Returns the new snapshot id.

    Returns:
        The integer id of the new ``brief_snapshots`` row.

    Refuses:
        Missing md file (raises :class:`FileNotFoundError`).
        Unwritten/unmigrated knowledge.db (sqlite3 raises directly).
    """
    install_dir = Path(install_dir)
    cfg = _config_for(install_dir)
    md_path = _resolve_md_path(install_dir, project)
    if not md_path.exists():
        raise FileNotFoundError(
            f"no brief md to snapshot for project={project!r} at {md_path}"
        )
    content = md_path.read_text(encoding="utf-8")
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    if line_count == 0 and content:
        line_count = 1

    if cfg.knowledge_db is None:
        raise RuntimeError("config has no knowledge_db path")
    conn = connect(cfg.knowledge_db)
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO brief_snapshots "
                "(project, md_path, content, line_count, saved_by, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project, str(md_path), content, line_count, saved_by, note),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def _resolve_md_path(install_dir: Path, project: str) -> Path:
    """Resolve the md path the regenerator considers canonical for ``project``.

    Prefers ``brief.md_path_for`` when the sibling module is loaded; otherwise
    falls back to ``_md_path_for`` (the documented convention).
    """
    try:
        brief_mod = importlib.import_module("runaway_context.brief")
    except ImportError:
        return _md_path_for(install_dir, project)
    fn = getattr(brief_mod, "md_path_for", None)
    if callable(fn):
        return Path(fn(install_dir=install_dir, project=project))
    return _md_path_for(install_dir, project)


# ---------------------------------------------------------------- listing

def list_snapshots(install_dir: Path, project: str,
                   limit: int = 10) -> List[Dict[str, Any]]:
    """Return up to ``limit`` recent snapshots for ``project``, newest first.

    Returns:
        A list of dicts with keys ``id``, ``project``, ``md_path``, ``line_count``,
        ``saved_at``, ``saved_by``, ``note``. ``content`` is omitted to keep
        listings cheap — fetch via :func:`get_snapshot` when needed.

    Refuses:
        Negative or zero ``limit`` (raises :class:`ValueError`).
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive int")
    cfg = _config_for(Path(install_dir))
    if cfg.knowledge_db is None:
        raise RuntimeError("config has no knowledge_db path")
    conn = connect(cfg.knowledge_db)
    try:
        rows = conn.execute(
            "SELECT id, project, md_path, line_count, saved_at, saved_by, note "
            "FROM brief_snapshots "
            "WHERE project = ? "
            "ORDER BY id DESC "
            "LIMIT ?",
            (project, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_snapshot(install_dir: Path, snapshot_id: int) -> Optional[Dict[str, Any]]:
    """Return the full content of a single snapshot, or None when missing.

    Returns:
        A dict with all snapshot columns including ``content``, or ``None``
        when no row matches ``snapshot_id``.

    Refuses:
        Non-int ``snapshot_id`` (raises :class:`TypeError`).
    """
    if not isinstance(snapshot_id, int):
        raise TypeError("snapshot_id must be an int")
    cfg = _config_for(Path(install_dir))
    if cfg.knowledge_db is None:
        raise RuntimeError("config has no knowledge_db path")
    conn = connect(cfg.knowledge_db)
    try:
        row = conn.execute(
            "SELECT id, project, md_path, content, line_count, "
            "       saved_at, saved_by, note "
            "FROM brief_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------- rollback

def rollback(install_dir: Path, project: str,
             snapshot_id: Optional[int] = None,
             *, actor: str) -> Dict[str, Any]:
    """Restore the md file for ``project`` from a snapshot.

    Behavior:
      * If ``snapshot_id`` is given, restore that exact snapshot — and refuse
        if the snapshot's ``project`` does not match.
      * Otherwise restore the most recent snapshot for ``project``.

    On success the md path on disk is overwritten with the snapshot's content
    and a row is appended to ``audit_log``.

    Returns:
        A dict ``{restored_from_id, md_path, line_count, project, actor}``.

    Refuses:
        Missing/empty ``actor`` (raises :class:`ValueError` — HR-3 + HR-7:
        every recoverable write is attributable).
        No snapshots for ``project`` (raises :class:`LookupError`).
        ``snapshot_id`` that belongs to a different project (raises
        :class:`ValueError`).
    """
    if not actor or not str(actor).strip():
        raise ValueError("rollback requires an explicit actor (HR-7)")
    actor = str(actor).strip()

    cfg = _config_for(Path(install_dir))
    if cfg.knowledge_db is None:
        raise RuntimeError("config has no knowledge_db path")
    conn = connect(cfg.knowledge_db)
    try:
        if snapshot_id is None:
            row = conn.execute(
                "SELECT id, project, md_path, content, line_count, saved_at "
                "FROM brief_snapshots "
                "WHERE project = ? "
                "ORDER BY id DESC LIMIT 1",
                (project,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, project, md_path, content, line_count, saved_at "
                "FROM brief_snapshots WHERE id = ?",
                (int(snapshot_id),),
            ).fetchone()
        if row is None:
            raise LookupError(
                f"no snapshot found for project={project!r} "
                f"(snapshot_id={snapshot_id})"
            )
        if row["project"] != project:
            raise ValueError(
                f"snapshot {snapshot_id} belongs to project "
                f"{row['project']!r}, not {project!r}"
            )

        md_path = Path(row["md_path"])
        content = row["content"]
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content, encoding="utf-8")

        with transaction(conn):
            _record_audit(
                conn,
                actor=actor,
                action="brief_rollback",
                target_id=int(row["id"]),
                details={
                    "project": project,
                    "md_path": str(md_path),
                    "line_count": int(row["line_count"]),
                    "saved_at": row["saved_at"],
                },
            )

        return {
            "restored_from_id": int(row["id"]),
            "md_path": str(md_path),
            "line_count": int(row["line_count"]),
            "project": project,
            "actor": actor,
        }
    finally:
        conn.close()
