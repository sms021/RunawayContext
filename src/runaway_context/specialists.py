"""Specialist agents (E15).

Specialists are first-class actors that own a curated slice of knowledge —
e.g. ``it_specialist``, ``accounting_specialist``, ``field_staff_specialist``.
Each specialist has a name + domain + (optionally) a markdown file path that
gets regenerated from its attached knowledge.

Schema:
    * ``specialists`` — one row per specialist.
    * ``specialist_knowledge`` — junction table: (specialist_id, table_name,
      record_id). ``table_name`` is constrained to ``knowledge_chunks`` or
      ``lessons_learned``.

Refuses:
    Attaching knowledge to a non-existent specialist (FK constraint).
    Detaching with an invalid table_name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from runaway_context._db import connect, transaction


_ALLOWED_TABLES = ("knowledge_chunks", "lessons_learned")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an ``sqlite3.Row`` to a plain dict.

    Returns:
        Dict keyed by column name.

    Refuses:
        Nothing.
    """
    return {k: row[k] for k in row.keys()}


def _validate_table(table: str) -> None:
    """Raise ``ValueError`` if ``table`` is not one of the allowed names.

    Returns:
        None.

    Refuses:
        ``table`` not in ``_ALLOWED_TABLES``.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(
            "table must be one of {allowed!r}, got {got!r}".format(
                allowed=_ALLOWED_TABLES, got=table
            )
        )


class SpecialistRegistry:
    """CRUD operations on the ``specialists`` and ``specialist_knowledge`` tables.

    The registry is a thin facade over SQL — it holds no mutable state across
    calls. Markdown regeneration is provided as a convenience for the brief
    rebuild flow; callers pass a ``md_dir`` to materialise per-specialist
    pages.
    """

    def __init__(self, knowledge_db: Path, md_dir: Optional[Path] = None) -> None:
        """Bind to a v3-migrated ``knowledge.db``.

        Args:
            knowledge_db: Path to the SQLite knowledge file.
            md_dir: Optional directory where ``regen_specialist_md`` will
                write specialist markdown when no per-row ``md_path`` is
                set.

        Returns:
            None.

        Refuses:
            Nothing — the path is not opened until first method call.
        """
        self._db_path = Path(knowledge_db)
        self._md_dir = Path(md_dir) if md_dir is not None else None

    # ----------------------------------------------------------- internal

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    def _fetch_one(self, specialist_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, name, domain, description, md_path, active, "
                "       created_at, updated_at "
                "FROM specialists WHERE id = ?",
                (int(specialist_id),),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    # ----------------------------------------------------------- public

    def register(
        self,
        *,
        name: str,
        domain: str,
        description: Optional[str] = None,
        md_path: Optional[str] = None,
    ) -> int:
        """Insert or upsert a specialist row by name.

        Idempotent on ``name``: re-registering an existing specialist updates
        domain / description / md_path and bumps ``updated_at``.

        Args:
            name: Unique specialist name (case-sensitive).
            domain: Free-text domain label (e.g. ``"accounting"``).
            description: Optional human-readable summary.
            md_path: Optional absolute path to the markdown file managed by
                this specialist.

        Returns:
            The specialist's row id.

        Raises:
            ValueError: when ``name`` or ``domain`` is empty.

        Refuses:
            Names / domains that are empty after stripping.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("domain must be a non-empty string")
        conn = self._connect()
        try:
            with transaction(conn):
                existing = conn.execute(
                    "SELECT id FROM specialists WHERE name = ?",
                    (name,),
                ).fetchone()
                if existing is None:
                    cur = conn.execute(
                        "INSERT INTO specialists (name, domain, description, md_path) "
                        "VALUES (?, ?, ?, ?)",
                        (name, domain, description, md_path),
                    )
                    return int(cur.lastrowid)
                row_id = int(existing[0])
                conn.execute(
                    "UPDATE specialists "
                    "SET domain = ?, description = COALESCE(?, description), "
                    "    md_path = COALESCE(?, md_path), "
                    "    updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (domain, description, md_path, row_id),
                )
                return row_id
        finally:
            conn.close()

    def list(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Return every registered specialist.

        Args:
            active_only: When True (default) filters to ``active = 1`` rows.

        Returns:
            List of dicts (id, name, domain, description, md_path, active,
            created_at, updated_at), ordered by name.

        Refuses:
            Nothing.
        """
        conn = self._connect()
        try:
            sql = (
                "SELECT id, name, domain, description, md_path, active, "
                "       created_at, updated_at "
                "FROM specialists "
            )
            if active_only:
                sql += "WHERE active = 1 "
            sql += "ORDER BY name"
            rows = conn.execute(sql).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def attach(self, *, specialist_id: int, table: str, record_id: int) -> None:
        """Attach a knowledge row to a specialist.

        Args:
            specialist_id: Existing specialist row id.
            table: One of ``knowledge_chunks`` or ``lessons_learned``.
            record_id: Row id within ``table``.

        Returns:
            None.

        Raises:
            ValueError: when ``table`` is not allowed.
            sqlite3.IntegrityError: when ``specialist_id`` is unknown.

        Refuses:
            Tables outside the allowlist (see ``_validate_table``).
        """
        _validate_table(table)
        conn = self._connect()
        try:
            with transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO specialist_knowledge "
                    "(specialist_id, table_name, record_id) "
                    "VALUES (?, ?, ?)",
                    (int(specialist_id), table, int(record_id)),
                )
                conn.execute(
                    "UPDATE specialists SET updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (int(specialist_id),),
                )
        finally:
            conn.close()

    def detach(self, *, specialist_id: int, table: str, record_id: int) -> None:
        """Remove an attachment.

        Args:
            specialist_id: Existing specialist row id.
            table: One of ``knowledge_chunks`` or ``lessons_learned``.
            record_id: Row id within ``table``.

        Returns:
            None.

        Raises:
            ValueError: when ``table`` is not allowed.

        Refuses:
            Tables outside the allowlist.
        """
        _validate_table(table)
        conn = self._connect()
        try:
            with transaction(conn):
                conn.execute(
                    "DELETE FROM specialist_knowledge "
                    "WHERE specialist_id = ? AND table_name = ? AND record_id = ?",
                    (int(specialist_id), table, int(record_id)),
                )
                conn.execute(
                    "UPDATE specialists SET updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (int(specialist_id),),
                )
        finally:
            conn.close()

    def knowledge_for(self, specialist_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Resolve a specialist's attached knowledge into row dicts.

        Soft-deleted rows are excluded.

        Args:
            specialist_id: Existing specialist row id.

        Returns:
            ``{"lessons": [..], "chunks": [..]}`` — each list contains the
            full active row dicts.

        Refuses:
            Returning soft-deleted rows.
        """
        conn = self._connect()
        try:
            lesson_rows = conn.execute(
                "SELECT l.* FROM lessons_learned l "
                "JOIN specialist_knowledge sk "
                "  ON sk.record_id = l.id AND sk.table_name = 'lessons_learned' "
                "WHERE sk.specialist_id = ? AND l.deleted_at IS NULL "
                "ORDER BY l.id",
                (int(specialist_id),),
            ).fetchall()
            chunk_rows = conn.execute(
                "SELECT c.* FROM knowledge_chunks c "
                "JOIN specialist_knowledge sk "
                "  ON sk.record_id = c.id AND sk.table_name = 'knowledge_chunks' "
                "WHERE sk.specialist_id = ? AND c.deleted_at IS NULL "
                "ORDER BY c.id",
                (int(specialist_id),),
            ).fetchall()
            return {
                "lessons": [_row_to_dict(r) for r in lesson_rows],
                "chunks": [_row_to_dict(r) for r in chunk_rows],
            }
        finally:
            conn.close()

    def regen_specialist_md(self, specialist_id: int) -> str:
        """Produce a specialist's markdown page from its attached knowledge.

        The output is a stable, deterministic markdown table summarising the
        specialist's lessons and chunks. If the specialist row has ``md_path``
        set (or ``self._md_dir`` is configured) the file is written. The
        content is also returned to the caller so it can be diffed, snapshot
        or written elsewhere.

        Args:
            specialist_id: Existing specialist row id.

        Returns:
            The generated markdown content.

        Raises:
            LookupError: when ``specialist_id`` is unknown.

        Refuses:
            Writing to disk when neither ``md_path`` nor ``md_dir`` is set.
        """
        meta = self._fetch_one(specialist_id)
        if meta is None:
            raise LookupError(
                "no specialist with id={id}".format(id=specialist_id)
            )
        bundle = self.knowledge_for(specialist_id)

        lines: List[str] = []
        lines.append("# Specialist: {name}".format(name=meta["name"]))
        lines.append("")
        lines.append("**Domain:** {d}".format(d=meta.get("domain") or "n/a"))
        if meta.get("description"):
            lines.append("")
            lines.append(meta["description"])
        lines.append("")
        lines.append("## Lessons ({n})".format(n=len(bundle["lessons"])))
        if bundle["lessons"]:
            lines.append("")
            for row in bundle["lessons"]:
                lines.append(
                    "- LL#{id} - {title}".format(
                        id=row["id"], title=row.get("title") or "(untitled)"
                    )
                )
        lines.append("")
        lines.append("## Chunks ({n})".format(n=len(bundle["chunks"])))
        if bundle["chunks"]:
            lines.append("")
            for row in bundle["chunks"]:
                lines.append(
                    "- src#{id} - {title}".format(
                        id=row["id"], title=row.get("title") or "(untitled)"
                    )
                )
        lines.append("")
        content = "\n".join(lines)

        target: Optional[Path] = None
        if meta.get("md_path"):
            target = Path(meta["md_path"])
        elif self._md_dir is not None:
            safe_name = "".join(
                ch if ch.isalnum() or ch in ("-", "_") else "_"
                for ch in str(meta["name"])
            )
            target = self._md_dir / "{name}.md".format(name=safe_name)

        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return content


def coverage_report(knowledge_db: Path) -> List[Dict[str, Any]]:
    """Summarise how much knowledge each specialist owns.

    Args:
        knowledge_db: Path to the v3-migrated knowledge DB.

    Returns:
        List of dicts, one per specialist:
        ``{specialist_id, name, domain, lessons_attached, chunks_attached,
        last_updated}``. Ordered by name.

    Refuses:
        Nothing — runs read-only.
    """
    conn = connect(Path(knowledge_db))
    try:
        rows = conn.execute(
            "SELECT s.id, s.name, s.domain, s.updated_at, "
            "  (SELECT COUNT(*) FROM specialist_knowledge sk "
            "     WHERE sk.specialist_id = s.id AND sk.table_name = 'lessons_learned'"
            "  ) AS lessons_attached, "
            "  (SELECT COUNT(*) FROM specialist_knowledge sk "
            "     WHERE sk.specialist_id = s.id AND sk.table_name = 'knowledge_chunks'"
            "  ) AS chunks_attached "
            "FROM specialists s "
            "ORDER BY s.name"
        ).fetchall()
        return [
            {
                "specialist_id": int(r["id"]),
                "name": r["name"],
                "domain": r["domain"],
                "lessons_attached": int(r["lessons_attached"]),
                "chunks_attached": int(r["chunks_attached"]),
                "last_updated": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()
