"""JSON export / import + conflict reporter (E23, T3 unlock).

Round-trip format::

    {
      "schema_version": "3.0.0",
      "exported_at": "<iso8601>",
      "lessons":      [<row dicts>],
      "chunks":       [<row dicts>],
      "specialists":  [<row dicts>],
      "data_sources": [<row dicts>]
    }

HR-3: importer never hard-deletes; conflicting rows are reported, not
silently overwritten, unless ``on_conflict='overwrite'`` is explicit.
HR-2: importer validates project_tags via the slug registry — rows with
unknown slugs are reported and refused.
Every insertion writes an audit_log entry.

Refuses:
    Importing into a non-migrated DB (slug_registry table missing).
    Returning silent success on a conflict — caller MUST be explicit
    about ``on_conflict``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from runaway_context._db import connect, transaction
from runaway_context.audit import append as audit_append
from runaway_context.errors import ConflictReported, InvalidProjectSlug
from runaway_context._slugs import is_valid_slug_format

SCHEMA_VERSION = "3.0.0"
_ALLOWED_CONFLICT_MODES = ("report", "skip", "overwrite")


def _iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        String like ``"2026-05-13T12:34:56Z"``.

    Refuses:
        Nothing.
    """
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an ``sqlite3.Row`` to a plain dict.

    Returns:
        Dict keyed by column name.

    Refuses:
        Nothing.
    """
    return {k: row[k] for k in row.keys()}


def _select_lessons(
    conn: sqlite3.Connection,
    *,
    project: Optional[str] = None,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM lessons_learned WHERE deleted_at IS NULL"
    params: List[Any] = []
    if project:
        sql += " AND (project = ? OR project_tags LIKE ?)"
        params.append(project)
        params.append("%{p}%".format(p=project))
    if not include_archived:
        sql += " AND COALESCE(maturity, 'active') NOT IN ('archived', 'superseded')"
    sql += " ORDER BY id"
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def _select_chunks(
    conn: sqlite3.Connection,
    *,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM knowledge_chunks WHERE deleted_at IS NULL"
    params: List[Any] = []
    if project:
        sql += " AND (project = ? OR project_tags LIKE ?)"
        params.append(project)
        params.append("%{p}%".format(p=project))
    sql += " ORDER BY id"
    return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def _select_specialists(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute("SELECT * FROM specialists ORDER BY id").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    return [_row_to_dict(r) for r in rows]


def _select_data_sources(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute("SELECT * FROM data_sources ORDER BY id").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    return [_row_to_dict(r) for r in rows]


def export_json(
    knowledge_db: Path,
    output_path: Path,
    *,
    project: Optional[str] = None,
    include_archived: bool = False,
) -> int:
    """Write a JSON file containing the exportable corpus.

    Args:
        knowledge_db: Path to ``knowledge.db``.
        output_path: File to write (overwrites). Parent dirs created.
        project: Optional canonical slug filter.
        include_archived: When False (default), excludes archived/superseded
            lessons.

    Returns:
        Total row count written across all sections.

    Refuses:
        Writing soft-deleted rows. HR-3-safe — the export is purely
        read-only.
    """
    conn = connect(Path(knowledge_db))
    try:
        lessons = _select_lessons(
            conn, project=project, include_archived=include_archived
        )
        chunks = _select_chunks(conn, project=project)
        specialists = _select_specialists(conn)
        data_sources = _select_data_sources(conn)
    finally:
        conn.close()

    payload = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _iso_now(),
        "project_filter": project,
        "include_archived": include_archived,
        "lessons": lessons,
        "chunks": chunks,
        "specialists": specialists,
        "data_sources": data_sources,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str))
    return len(lessons) + len(chunks) + len(specialists) + len(data_sources)


@dataclass
class _ConflictRecord:
    """Internal: minimal conflict representation."""

    table: str
    key: Dict[str, Any]
    local: Dict[str, Any]
    incoming: Dict[str, Any]
    reason: str


def _project_tags_set(value: Any) -> List[str]:
    """Parse ``project_tags`` (JSON list as text) safely.

    Returns:
        List of slug strings (may be empty).

    Refuses:
        Nothing — malformed input returns empty list (the upstream HR-2
        guard re-checks at write time).
    """
    if isinstance(value, list):
        return [s for s in value if isinstance(s, str)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, str)]
    return []


def _known_slugs(conn: sqlite3.Connection) -> set:
    try:
        rows = conn.execute("SELECT slug FROM slug_registry").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return set()
        raise
    return {r[0] for r in rows}


def _validate_tags(tags: List[str], known: set) -> List[str]:
    """Return any tag that fails the registry check or canonical format.

    Returns:
        List of offending tag strings.

    Refuses:
        Nothing — surfaces the bad ones for the caller to report.
    """
    bad: List[str] = []
    for t in tags:
        if not is_valid_slug_format(t):
            bad.append(t)
            continue
        if known and t not in known:
            bad.append(t)
    return bad


def _row_signature(row: Dict[str, Any], fields: List[str]) -> str:
    """Build a comparable signature from ``fields`` of a row dict.

    Returns:
        Newline-joined string of normalised field values.

    Refuses:
        Nothing.
    """
    parts: List[str] = []
    for f in fields:
        v = row.get(f)
        parts.append("" if v is None else str(v).strip())
    return "\n".join(parts)


_LESSON_SIG_FIELDS = ["title", "what_happened", "why", "prevention_rule"]
_CHUNK_SIG_FIELDS = ["title", "topic", "body"]


def _existing_lesson(
    conn: sqlite3.Connection, slug: Optional[str], title: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not title:
        return None
    if slug:
        row = conn.execute(
            "SELECT * FROM lessons_learned WHERE slug = ? AND title = ? "
            "  AND deleted_at IS NULL LIMIT 1",
            (slug, title),
        ).fetchone()
        if row is not None:
            return _row_to_dict(row)
    row = conn.execute(
        "SELECT * FROM lessons_learned WHERE title = ? AND deleted_at IS NULL "
        "ORDER BY id LIMIT 1",
        (title,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _existing_chunk(
    conn: sqlite3.Connection, project: Optional[str], topic: Optional[str], title: Optional[str]
) -> Optional[Dict[str, Any]]:
    if project and topic:
        row = conn.execute(
            "SELECT * FROM knowledge_chunks WHERE project = ? AND topic = ? "
            "  AND deleted_at IS NULL LIMIT 1",
            (project, topic),
        ).fetchone()
        if row is not None:
            return _row_to_dict(row)
    if title:
        row = conn.execute(
            "SELECT * FROM knowledge_chunks WHERE title = ? AND deleted_at IS NULL "
            "LIMIT 1",
            (title,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    return None


def _conflict_to_dict(c: _ConflictRecord) -> Dict[str, Any]:
    return {
        "table": c.table,
        "key": c.key,
        "local": c.local,
        "incoming": c.incoming,
        "reason": c.reason,
    }


def _insert_lesson(
    conn: sqlite3.Connection, row: Dict[str, Any], actor: str
) -> int:
    cols = [
        "project", "title", "lesson", "context", "slug", "what_happened",
        "why", "the_fix", "prevention_rule", "severity", "status",
        "project_tags", "source_conversation_ref", "date_learned",
        "last_revisited", "maturity", "blast_radius", "frequency",
        "reversibility", "author_id", "visibility",
    ]
    values = [row.get(c) for c in cols]
    # Ensure project_tags is JSON text (HR-2 trigger expects non-empty array).
    pt = row.get("project_tags")
    if isinstance(pt, list):
        values[cols.index("project_tags")] = json.dumps(pt)
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(
        "INSERT INTO lessons_learned ({c}) VALUES ({p})".format(
            c=",".join(cols), p=placeholders
        ),
        values,
    )
    new_id = int(cur.lastrowid)
    audit_append(
        conn,
        actor=actor,
        action="import.lesson",
        target_table="lessons_learned",
        target_id=new_id,
        details={"title": row.get("title"), "slug": row.get("slug")},
    )
    return new_id


def _insert_chunk(
    conn: sqlite3.Connection, row: Dict[str, Any], actor: str
) -> int:
    cols = [
        "project", "project_tags", "topic", "title", "body", "tags",
        "author_id", "visibility",
    ]
    values = [row.get(c) for c in cols]
    pt = row.get("project_tags")
    if isinstance(pt, list):
        values[cols.index("project_tags")] = json.dumps(pt)
    tags = row.get("tags")
    if isinstance(tags, list):
        values[cols.index("tags")] = json.dumps(tags)
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(
        "INSERT INTO knowledge_chunks ({c}) VALUES ({p})".format(
            c=",".join(cols), p=placeholders
        ),
        values,
    )
    new_id = int(cur.lastrowid)
    audit_append(
        conn,
        actor=actor,
        action="import.chunk",
        target_table="knowledge_chunks",
        target_id=new_id,
        details={"title": row.get("title"), "topic": row.get("topic")},
    )
    return new_id


def _overwrite_lesson(
    conn: sqlite3.Connection, existing_id: int, row: Dict[str, Any], actor: str
) -> None:
    updatable = [
        "what_happened", "why", "the_fix", "prevention_rule", "severity",
        "project_tags", "blast_radius", "frequency", "reversibility",
        "visibility",
    ]
    sets = []
    params: List[Any] = []
    for c in updatable:
        if c in row:
            sets.append("{c} = ?".format(c=c))
            v = row[c]
            if c == "project_tags" and isinstance(v, list):
                v = json.dumps(v)
            params.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(existing_id)
    conn.execute(
        "UPDATE lessons_learned SET {s} WHERE id = ? AND deleted_at IS NULL".format(
            s=", ".join(sets)
        ),
        params,
    )
    audit_append(
        conn,
        actor=actor,
        action="import.lesson.overwrite",
        target_table="lessons_learned",
        target_id=int(existing_id),
        details={"updated_fields": [c for c in updatable if c in row]},
    )


def _overwrite_chunk(
    conn: sqlite3.Connection, existing_id: int, row: Dict[str, Any], actor: str
) -> None:
    updatable = ["body", "tags", "project_tags", "visibility"]
    sets = []
    params: List[Any] = []
    for c in updatable:
        if c in row:
            sets.append("{c} = ?".format(c=c))
            v = row[c]
            if c in ("project_tags", "tags") and isinstance(v, list):
                v = json.dumps(v)
            params.append(v)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(existing_id)
    conn.execute(
        "UPDATE knowledge_chunks SET {s} WHERE id = ? AND deleted_at IS NULL".format(
            s=", ".join(sets)
        ),
        params,
    )
    audit_append(
        conn,
        actor=actor,
        action="import.chunk.overwrite",
        target_table="knowledge_chunks",
        target_id=int(existing_id),
        details={"updated_fields": [c for c in updatable if c in row]},
    )


def import_json(
    knowledge_db: Path,
    input_path: Path,
    *,
    actor: str,
    on_conflict: str = "report",
) -> Dict[str, Any]:
    """Read a JSON export and apply it under the chosen conflict policy.

    Args:
        knowledge_db: Path to ``knowledge.db``.
        input_path: Path to the JSON file.
        actor: Opaque author_id of the importer (audited).
        on_conflict: ``"report"`` (default, raises if any conflict),
            ``"skip"`` (skip conflicting rows), or ``"overwrite"`` (replace
            the local row's mutable fields).

    Returns:
        ``{added: int, skipped: int, conflicts: [...]}``.

    Raises:
        ValueError: when ``on_conflict`` is not one of the allowed modes.
        FileNotFoundError: when ``input_path`` is missing.
        ConflictReported: when ``on_conflict='report'`` and at least one
            row-level conflict was detected.
        InvalidProjectSlug: when an incoming row references a slug that
            is not in the registry (HR-2).

    Refuses:
        Silently overwriting local data — explicit mode required.
    """
    if on_conflict not in _ALLOWED_CONFLICT_MODES:
        raise ValueError(
            "on_conflict must be one of {a!r}".format(a=_ALLOWED_CONFLICT_MODES)
        )
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))
    payload = json.loads(input_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("import payload must be a JSON object")

    incoming_lessons = list(payload.get("lessons", []))
    incoming_chunks = list(payload.get("chunks", []))
    incoming_specialists = list(payload.get("specialists", []))
    incoming_data_sources = list(payload.get("data_sources", []))

    added = 0
    skipped = 0
    conflicts: List[_ConflictRecord] = []
    bad_slug_rows: List[Dict[str, Any]] = []

    conn = connect(Path(knowledge_db))
    try:
        known = _known_slugs(conn)
        if not known:
            # HR-2: without a registry there's no way to validate writes.
            raise InvalidProjectSlug(
                "slug_registry is empty or missing — register slugs before import"
            )

        with transaction(conn):
            # --- lessons --------------------------------------------------
            for row in incoming_lessons:
                tags = _project_tags_set(row.get("project_tags"))
                if not tags:
                    bad_slug_rows.append(
                        {"kind": "lesson", "title": row.get("title"), "tags": tags}
                    )
                    skipped += 1
                    continue
                bad = _validate_tags(tags, known)
                if bad:
                    bad_slug_rows.append(
                        {
                            "kind": "lesson",
                            "title": row.get("title"),
                            "bad_slugs": bad,
                        }
                    )
                    skipped += 1
                    continue
                local = _existing_lesson(
                    conn, row.get("slug"), row.get("title")
                )
                if local is None:
                    _insert_lesson(conn, row, actor)
                    added += 1
                    continue
                local_sig = _row_signature(local, _LESSON_SIG_FIELDS)
                incoming_sig = _row_signature(row, _LESSON_SIG_FIELDS)
                if local_sig == incoming_sig:
                    skipped += 1
                    continue
                conflicts.append(
                    _ConflictRecord(
                        table="lessons_learned",
                        key={"slug": row.get("slug"), "title": row.get("title")},
                        local=local,
                        incoming=row,
                        reason="content differs",
                    )
                )
                if on_conflict == "overwrite":
                    _overwrite_lesson(conn, int(local["id"]), row, actor)
                    added += 1
                elif on_conflict == "skip":
                    skipped += 1

            # --- chunks ---------------------------------------------------
            for row in incoming_chunks:
                tags = _project_tags_set(row.get("project_tags"))
                if not tags:
                    bad_slug_rows.append(
                        {"kind": "chunk", "title": row.get("title"), "tags": tags}
                    )
                    skipped += 1
                    continue
                bad = _validate_tags(tags, known)
                if bad:
                    bad_slug_rows.append(
                        {"kind": "chunk", "title": row.get("title"), "bad_slugs": bad}
                    )
                    skipped += 1
                    continue
                local = _existing_chunk(
                    conn, row.get("project"), row.get("topic"), row.get("title")
                )
                if local is None:
                    _insert_chunk(conn, row, actor)
                    added += 1
                    continue
                local_sig = _row_signature(local, _CHUNK_SIG_FIELDS)
                incoming_sig = _row_signature(row, _CHUNK_SIG_FIELDS)
                if local_sig == incoming_sig:
                    skipped += 1
                    continue
                conflicts.append(
                    _ConflictRecord(
                        table="knowledge_chunks",
                        key={"project": row.get("project"), "topic": row.get("topic")},
                        local=local,
                        incoming=row,
                        reason="content differs",
                    )
                )
                if on_conflict == "overwrite":
                    _overwrite_chunk(conn, int(local["id"]), row, actor)
                    added += 1
                elif on_conflict == "skip":
                    skipped += 1

            # --- specialists ---------------------------------------------
            for row in incoming_specialists:
                name = row.get("name")
                if not name:
                    skipped += 1
                    continue
                existing = conn.execute(
                    "SELECT id FROM specialists WHERE name = ?", (name,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO specialists (name, domain, description, md_path) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            name,
                            row.get("domain", "general"),
                            row.get("description"),
                            row.get("md_path"),
                        ),
                    )
                    audit_append(
                        conn,
                        actor=actor,
                        action="import.specialist",
                        target_table="specialists",
                        target_id=int(conn.execute("SELECT last_insert_rowid()").fetchone()[0]),
                        details={"name": name},
                    )
                    added += 1
                else:
                    skipped += 1

            # --- data_sources --------------------------------------------
            for row in incoming_data_sources:
                system = row.get("system")
                name = row.get("name")
                kind = row.get("kind") or "other"
                if not system or not name:
                    skipped += 1
                    continue
                existing = conn.execute(
                    "SELECT id FROM data_sources WHERE system = ? AND name = ?",
                    (system, name),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO data_sources "
                        "(system, name, kind, description, project) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (system, name, kind, row.get("description"), row.get("project")),
                    )
                    audit_append(
                        conn,
                        actor=actor,
                        action="import.data_source",
                        target_table="data_sources",
                        target_id=int(conn.execute("SELECT last_insert_rowid()").fetchone()[0]),
                        details={"system": system, "name": name},
                    )
                    added += 1
                else:
                    skipped += 1
    finally:
        conn.close()

    result: Dict[str, Any] = {
        "added": added,
        "skipped": skipped,
        "conflicts": [_conflict_to_dict(c) for c in conflicts],
        "bad_slug_rows": bad_slug_rows,
    }

    if bad_slug_rows:
        raise InvalidProjectSlug(
            "{n} incoming row(s) reference unregistered or invalid slugs; "
            "see result['bad_slug_rows']".format(n=len(bad_slug_rows))
        )

    if conflicts and on_conflict == "report":
        raise ConflictReported(
            "{n} conflict(s) detected; rerun with --on-conflict=skip|overwrite".format(
                n=len(conflicts)
            )
        )

    return result


class ConflictReporter:
    """Render JSON import conflicts to a markdown report.

    Holds no mutable state — purely a formatter.
    """

    def report(
        self,
        conflicts: List[Dict[str, Any]],
        output_path: Optional[Path] = None,
    ) -> str:
        """Render ``conflicts`` as markdown.

        Args:
            conflicts: List of dicts produced by :func:`import_json`.
            output_path: Optional path; when given, file is written.

        Returns:
            Markdown content as a string.

        Refuses:
            Returning an empty string for a non-empty input — empty
            conflict list still produces a header so downstream automation
            can detect the run happened.
        """
        lines: List[str] = []
        lines.append("# Import Conflict Report")
        lines.append("")
        lines.append(
            "Conflicts found: **{n}**".format(n=len(conflicts))
        )
        lines.append("")
        if not conflicts:
            lines.append("_No conflicts. Import is safe to apply._")
        for i, c in enumerate(conflicts, start=1):
            lines.append("## {i}. {tbl}  ({reason})".format(
                i=i, tbl=c.get("table", "?"), reason=c.get("reason", "?")
            ))
            lines.append("")
            lines.append("**Key:** `{k}`".format(k=json.dumps(c.get("key", {}))))
            lines.append("")
            lines.append("**Local:**")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(c.get("local", {}), indent=2, default=str))
            lines.append("```")
            lines.append("")
            lines.append("**Incoming:**")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(c.get("incoming", {}), indent=2, default=str))
            lines.append("```")
            lines.append("")
        content = "\n".join(lines)
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content)
        return content
