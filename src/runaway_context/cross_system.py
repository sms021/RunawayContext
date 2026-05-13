"""Cross-system data map (E16, T2.5).

The ``DataMap`` registers data sources from heterogeneous systems (Vista,
Procore, Monday, FileMaker, OPC, ...) and the mappings between them so a
human or an AI can ask "where does this field live?" or "how does this join
to that?" without crawling source code.

Schema:
    * ``data_sources`` (system, name, kind, description, project) UNIQUE
      (system, name).
    * ``data_source_mappings`` (from_source, to_source, join_on, notes)
      UNIQUE (from_source, to_source, join_on).

Refuses:
    Adding a source with an unrecognised ``kind`` (CHECK constraint in DDL).
    Adding a mapping that references a missing source (FK constraint).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from runaway_context._db import connect, transaction


_ALLOWED_KINDS = ("table", "view", "endpoint", "file", "queue", "other")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an ``sqlite3.Row`` to a plain dict.

    Returns:
        Dict keyed by column name.

    Refuses:
        Nothing.
    """
    return {k: row[k] for k in row.keys()}


class DataMap:
    """Read/write API over the cross-system data map tables.

    Holds no mutable state between calls — each method opens its own
    connection.
    """

    def __init__(self, knowledge_db: Path) -> None:
        """Bind to a v3-migrated ``knowledge.db``.

        Returns:
            None.

        Refuses:
            Nothing.
        """
        self._db_path = Path(knowledge_db)

    # ----------------------------------------------------------- internal

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    def _source_id(
        self, conn: sqlite3.Connection, system: str, name: str
    ) -> Optional[int]:
        row = conn.execute(
            "SELECT id FROM data_sources WHERE system = ? AND name = ?",
            (system, name),
        ).fetchone()
        if row is None:
            return None
        return int(row[0])

    # ----------------------------------------------------------- public

    def add_source(
        self,
        *,
        system: str,
        name: str,
        kind: str,
        description: Optional[str] = None,
        project: Optional[str] = None,
    ) -> int:
        """Register a new data source. Idempotent on ``(system, name)``.

        Args:
            system: Origin system label, lowercased by convention
                (``"vista"``, ``"procore"``, ``"monday"``).
            name: Table / endpoint / queue / file path identifier.
            kind: One of ``table``, ``view``, ``endpoint``, ``file``,
                ``queue``, ``other``.
            description: Optional human-readable summary.
            project: Optional canonical project slug.

        Returns:
            The data source's row id (new or existing).

        Raises:
            ValueError: when ``system`` or ``name`` is empty, or ``kind`` is
                not in the allowlist.

        Refuses:
            Inserts blocked by the DDL CHECK constraint surface as
            ``sqlite3.IntegrityError``.
        """
        if not isinstance(system, str) or not system.strip():
            raise ValueError("system must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if kind not in _ALLOWED_KINDS:
            raise ValueError(
                "kind must be one of {allowed!r}, got {got!r}".format(
                    allowed=_ALLOWED_KINDS, got=kind
                )
            )
        conn = self._connect()
        try:
            with transaction(conn):
                existing = self._source_id(conn, system, name)
                if existing is not None:
                    conn.execute(
                        "UPDATE data_sources "
                        "SET kind = ?, "
                        "    description = COALESCE(?, description), "
                        "    project = COALESCE(?, project) "
                        "WHERE id = ?",
                        (kind, description, project, existing),
                    )
                    return existing
                cur = conn.execute(
                    "INSERT INTO data_sources (system, name, kind, description, project) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (system, name, kind, description, project),
                )
                return int(cur.lastrowid)
        finally:
            conn.close()

    def add_mapping(
        self,
        *,
        from_system: str,
        from_name: str,
        to_system: str,
        to_name: str,
        join_on: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Register a mapping between two existing data sources.

        Args:
            from_system, from_name: Source-side coordinates (must already
                exist in ``data_sources``).
            to_system, to_name: Target-side coordinates (must already
                exist).
            join_on: Optional join expression (``"id = vendor_id"``).
            notes: Optional free-text annotation.

        Returns:
            The mapping row id (new or existing).

        Raises:
            LookupError: when either endpoint does not exist.

        Refuses:
            Mapping endpoints that have not been registered first.
        """
        conn = self._connect()
        try:
            with transaction(conn):
                src_id = self._source_id(conn, from_system, from_name)
                if src_id is None:
                    raise LookupError(
                        "from source {s}:{n!r} is not registered".format(
                            s=from_system, n=from_name
                        )
                    )
                tgt_id = self._source_id(conn, to_system, to_name)
                if tgt_id is None:
                    raise LookupError(
                        "to source {s}:{n!r} is not registered".format(
                            s=to_system, n=to_name
                        )
                    )
                existing = conn.execute(
                    "SELECT id FROM data_source_mappings "
                    "WHERE from_source = ? AND to_source = ? "
                    "  AND COALESCE(join_on, '') = COALESCE(?, '')",
                    (src_id, tgt_id, join_on),
                ).fetchone()
                if existing is not None:
                    row_id = int(existing[0])
                    conn.execute(
                        "UPDATE data_source_mappings "
                        "SET notes = COALESCE(?, notes) WHERE id = ?",
                        (notes, row_id),
                    )
                    return row_id
                cur = conn.execute(
                    "INSERT INTO data_source_mappings "
                    "(from_source, to_source, join_on, notes) "
                    "VALUES (?, ?, ?, ?)",
                    (src_id, tgt_id, join_on, notes),
                )
                return int(cur.lastrowid)
        finally:
            conn.close()

    def find_sources(
        self,
        system: Optional[str] = None,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search registered data sources.

        Args:
            system: Optional system filter (exact match).
            query: Optional substring to match against name or description
                (case-insensitive).

        Returns:
            List of source dicts ordered by (system, name).

        Refuses:
            Nothing — returns empty list when nothing matches.
        """
        conn = self._connect()
        try:
            sql = (
                "SELECT id, system, name, kind, description, project, created_at "
                "FROM data_sources WHERE 1=1"
            )
            params: List[Any] = []
            if system:
                sql += " AND system = ?"
                params.append(system)
            if query:
                sql += " AND (LOWER(name) LIKE ? OR LOWER(COALESCE(description, '')) LIKE ?)"
                like = "%{q}%".format(q=query.lower())
                params.append(like)
                params.append(like)
            sql += " ORDER BY system, name"
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def trace(
        self, system: str, name: str, depth: int = 3
    ) -> List[Dict[str, Any]]:
        """Walk mappings forward from a starting source.

        Args:
            system: Starting system.
            name: Starting source name.
            depth: Maximum hop count (default 3).

        Returns:
            Ordered list of edges visited::

                [{"step": 1, "from": {...}, "to": {...}, "join_on": "...",
                  "notes": "..."}]

            Empty list when no edges exist.

        Raises:
            LookupError: when the starting source is not registered.

        Refuses:
            Negative depth.
        """
        if depth < 0:
            raise ValueError("depth must be non-negative")
        conn = self._connect()
        try:
            start_id = self._source_id(conn, system, name)
            if start_id is None:
                raise LookupError(
                    "source {s}:{n!r} is not registered".format(s=system, n=name)
                )
            visited: List[int] = [start_id]
            frontier: List[int] = [start_id]
            edges: List[Dict[str, Any]] = []
            step = 0
            while frontier and step < depth:
                step += 1
                next_frontier: List[int] = []
                for current in frontier:
                    rows = conn.execute(
                        "SELECT m.id, m.from_source, m.to_source, m.join_on, m.notes, "
                        "       fs.system AS from_system, fs.name AS from_name, "
                        "       fs.kind AS from_kind, "
                        "       ts.system AS to_system, ts.name AS to_name, "
                        "       ts.kind AS to_kind "
                        "FROM data_source_mappings m "
                        "JOIN data_sources fs ON fs.id = m.from_source "
                        "JOIN data_sources ts ON ts.id = m.to_source "
                        "WHERE m.from_source = ? "
                        "ORDER BY m.id",
                        (current,),
                    ).fetchall()
                    for r in rows:
                        edges.append(
                            {
                                "step": step,
                                "mapping_id": int(r["id"]),
                                "from": {
                                    "system": r["from_system"],
                                    "name": r["from_name"],
                                    "kind": r["from_kind"],
                                },
                                "to": {
                                    "system": r["to_system"],
                                    "name": r["to_name"],
                                    "kind": r["to_kind"],
                                },
                                "join_on": r["join_on"],
                                "notes": r["notes"],
                            }
                        )
                        tgt = int(r["to_source"])
                        if tgt not in visited:
                            visited.append(tgt)
                            next_frontier.append(tgt)
                frontier = next_frontier
            return edges
        finally:
            conn.close()

    def export_map(self, output_path: Path) -> int:
        """Write the complete cross-system map as markdown tables.

        The output has two sections: ``## Sources`` and ``## Mappings``,
        each rendered as a markdown table.

        Args:
            output_path: Path to write. Parent dirs are created if missing.

        Returns:
            Number of lines written (file is overwritten on each call).

        Refuses:
            Nothing — empty map produces a header-only file.
        """
        conn = self._connect()
        try:
            source_rows = conn.execute(
                "SELECT id, system, name, kind, description, project "
                "FROM data_sources ORDER BY system, name"
            ).fetchall()
            mapping_rows = conn.execute(
                "SELECT m.id, "
                "       fs.system AS from_system, fs.name AS from_name, "
                "       ts.system AS to_system, ts.name AS to_name, "
                "       m.join_on, m.notes "
                "FROM data_source_mappings m "
                "JOIN data_sources fs ON fs.id = m.from_source "
                "JOIN data_sources ts ON ts.id = m.to_source "
                "ORDER BY fs.system, fs.name, ts.system, ts.name"
            ).fetchall()
        finally:
            conn.close()

        lines: List[str] = []
        lines.append("# Cross-System Data Map")
        lines.append("")
        lines.append("## Sources ({n})".format(n=len(source_rows)))
        lines.append("")
        lines.append("| System | Name | Kind | Project | Description |")
        lines.append("|---|---|---|---|---|")
        for r in source_rows:
            lines.append(
                "| {sy} | {nm} | {k} | {pj} | {de} |".format(
                    sy=r["system"],
                    nm=r["name"],
                    k=r["kind"],
                    pj=r["project"] or "",
                    de=(r["description"] or "").replace("|", "\\|"),
                )
            )
        lines.append("")
        lines.append("## Mappings ({n})".format(n=len(mapping_rows)))
        lines.append("")
        lines.append("| From | To | Join | Notes |")
        lines.append("|---|---|---|---|")
        for r in mapping_rows:
            lines.append(
                "| {fs}:{fn} | {ts}:{tn} | {j} | {no} |".format(
                    fs=r["from_system"],
                    fn=r["from_name"],
                    ts=r["to_system"],
                    tn=r["to_name"],
                    j=(r["join_on"] or "").replace("|", "\\|"),
                    no=(r["notes"] or "").replace("|", "\\|"),
                )
            )
        lines.append("")
        content = "\n".join(lines)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        return len(lines)
