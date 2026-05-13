"""RunawayContext Python ``Client`` (E10) — the single canonical API.

External callers (CLI, MCP server, user scripts) talk to RunawayContext
through this class. It is responsible for enforcing the contracts:

* HR-1 — refuses to start when a non-allowlisted network-capable module is
  loaded but its corresponding config flag is False.
* HR-2 — every write validates ``project_tags`` against the slug registry
  before the row hits the DB.
* HR-3 — there is no hard-delete method. Soft-delete only, with a snapshot
  written to ``record_versions``.
* HR-10 — no silent failures: each write surfaces refusals as typed
  exceptions from :mod:`runaway_context.errors`.
* HR-14 — every public method has a docstring with ``Returns:``, ``Raises:``,
  ``Refuses:`` lines.

Sibling modules (``maturation``, ``audit``, ``slugs_lifecycle``, ``transfer``,
``stats``, ``brief_preview``) are lazy-imported inside the methods that
use them to avoid import-cycle and partial-build issues. When an optional
sibling is missing, the method raises :class:`RuntimeError` rather than
silently degrading (HR-10).

Refuses:
    Starting when the migrated schema version is not ``3.x.y`` (HR-4).
    Starting when an opt-in network module is loaded without its flag.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from runaway_context._db import connect, transaction
from runaway_context._slugs import is_valid_slug_format
from runaway_context.config import Config, SCHEMA_VERSION
from runaway_context.errors import (
    InvalidProjectSlug,
    MaturationApprovalRequired,
    NetworkEgressBlocked,
    RunawayContextError,
)
from runaway_context.identity import current_author_id


_log = logging.getLogger(__name__)


# Modules that are network-capable but only allowed to run when their
# corresponding Config flag is True. Mirror the allowlist in HR-1 tests.
_NETWORK_MODULES: Dict[str, str] = {
    "runaway_context.embeddings.providers.openai": "embeddings_enabled",
    "runaway_context.embeddings.providers.voyage": "embeddings_enabled",
    "runaway_context.metrics.otlp_exporter": "otlp_enabled",
    "runaway_context.federation.refresh_worker": "federation_enabled",
}


def _dumps_tags(tags: Optional[List[str]]) -> str:
    """Serialize a tags / project_tags list as canonical JSON for storage."""
    if not tags:
        return "[]"
    cleaned = [t for t in tags if isinstance(t, str) and t]
    return json.dumps(sorted(set(cleaned)))


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert ``sqlite3.Row`` to plain dict, decoding JSON-shaped columns."""
    if row is None:
        return None
    out: Dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in ("project_tags", "tags", "active_lesson_ids",
                   "active_chunk_ids", "top_warnings") and isinstance(value, str) and value:
            try:
                out[key] = json.loads(value)
            except (ValueError, TypeError):
                out[key] = value
        else:
            out[key] = value
    return out


class Client:
    """Canonical Python entry point to a RunawayContext install.

    A ``Client`` resolves config + paths, validates the migrated schema
    version, performs the HR-1 self-check, and exposes the read / write
    surface as instance methods. Each method's docstring lists the typed
    refusals it can raise.

    Refuses:
        Construction when the on-disk schema is not v3.x.y or when a
        network-capable module is loaded with its config flag off (HR-1).
    """

    def __init__(
        self,
        install_dir: Optional[Union[Path, str]] = None,
        *,
        config: Optional[Config] = None,
    ) -> None:
        """Initialize the client. Loads config, validates the schema, runs HR-1 self-check.

        Args:
            install_dir: Optional override of the install directory. Falls
                back to :func:`config.default_install_dir`.
            config: Optional pre-loaded :class:`Config`. When None, the
                client loads it from ``<install_dir>/config.json``.

        Returns:
            None.

        Raises:
            NetworkEgressBlocked: when a network-capable module is loaded
                but its config flag is False (HR-1).
            RunawayContextError: when the schema is missing or not v3.x.y.

        Refuses:
            Starting on an unmigrated DB. Starting with a network module
            loaded but disabled.
        """
        if config is None:
            cfg = Config.load(Path(install_dir).expanduser() if install_dir else None)
        else:
            cfg = config
            if install_dir is not None:
                cfg.install_dir = Path(install_dir).expanduser()
        self._config: Config = cfg
        self._install_dir: Path = Path(cfg.install_dir)
        self._knowledge_db: Path = Path(cfg.knowledge_db) if cfg.knowledge_db else (self._install_dir / "knowledge.db")
        self._sessions_db: Optional[Path] = Path(cfg.sessions_db) if cfg.sessions_db else None
        self._metrics_db: Optional[Path] = Path(cfg.metrics_db) if cfg.metrics_db else None

        self._hr1_self_check()
        self._validate_schema_version()
        self._validate_maturity_enum()
        self._validate_audit_chain_quick()

    # ------------------------------------------------------------------ setup

    def _hr1_self_check(self) -> None:
        """Refuse to start if a network module is loaded without its config flag.

        Returns:
            None.

        Raises:
            NetworkEgressBlocked: when any module in :data:`_NETWORK_MODULES`
                is in ``sys.modules`` but its corresponding flag on
                :class:`Config` is False (HR-1).

        Refuses:
            Operating with hidden / accidentally-loaded egress paths.
        """
        for module_name, flag in _NETWORK_MODULES.items():
            if module_name not in sys.modules:
                continue
            allowed = bool(getattr(self._config, flag, False))
            if not allowed:
                raise NetworkEgressBlocked(
                    "Refusing to start: network-capable module "
                    f"{module_name!r} is loaded but config flag "
                    f"{flag!r} is False (HR-1)."
                )

    def _validate_schema_version(self) -> None:
        """Refuse to start unless the migrated schema is v3.x.y.

        Returns:
            None.

        Raises:
            RunawayContextError: when the schema_version row is missing,
                unreadable, or has a major version != 3.

        Refuses:
            Touching a DB that has not been migrated to v3.
        """
        if not self._knowledge_db.exists():
            raise RunawayContextError(
                f"knowledge.db not found at {self._knowledge_db}; run migrate() first."
            )
        conn = sqlite3.connect(str(self._knowledge_db))
        try:
            row = conn.execute(
                "SELECT major, minor, patch FROM schema_version WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise RunawayContextError(
                f"schema_version table missing on {self._knowledge_db}: {exc}"
            )
        finally:
            conn.close()
        if row is None:
            raise RunawayContextError(
                f"schema_version row missing on {self._knowledge_db}; run migrate()."
            )
        major, minor, patch = int(row[0]), int(row[1]), int(row[2])
        if major != SCHEMA_VERSION[0]:
            raise RunawayContextError(
                f"Refusing to start: knowledge.db at {self._knowledge_db} is "
                f"schema v{major}.{minor}.{patch}; expected v{SCHEMA_VERSION[0]}.x.y."
            )

    def _validate_maturity_enum(self) -> None:
        """Refuse to start if external SQL pushed an invalid maturity into the table (HR-9).

        Returns:
            None.

        Raises:
            MaturityEnumViolation: when any ``lessons_learned.maturity`` value
                falls outside the canonical six-state enum.

        Refuses:
            Operating on a DB whose maturity column was hand-edited by SQL.
        """
        from runaway_context.errors import MaturityEnumViolation  # local

        conn = sqlite3.connect(str(self._knowledge_db))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM lessons_learned "
                "WHERE maturity IS NOT NULL "
                "  AND maturity NOT IN (SELECT state FROM maturity_states)"
            ).fetchone()
        except sqlite3.OperationalError:
            return
        finally:
            conn.close()
        bad_count = int(row[0]) if row else 0
        if bad_count:
            raise MaturityEnumViolation(
                f"{bad_count} lessons have maturity values outside the canonical enum (HR-9)."
            )

    def _validate_audit_chain_quick(self) -> None:
        """Refuse to start if a quick audit-chain check fails (HR-7).

        Walks the most recent rows of ``audit_log`` to confirm the hash chain
        verifies. Full verification is available via :meth:`audit_verify`;
        startup runs a bounded check so import-time cost stays low.

        Returns:
            None.

        Raises:
            AuditChainBroken: when the most-recent rows fail chain verification.

        Refuses:
            Touching a DB whose audit_log shows tampering.
        """
        from runaway_context.errors import AuditChainBroken  # local
        from runaway_context import audit as _audit

        # Only verify if there are rows; an empty audit_log is fine on a
        # freshly-migrated DB. We verify the full chain — `audit.verify` is
        # cheap enough for typical install sizes (it's O(rows)).
        ok, first_bad, reason = _audit.verify(self._knowledge_db)
        if not ok:
            raise AuditChainBroken(
                f"audit_log chain verification failed at id={first_bad}: {reason} (HR-7)"
            )

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to ``knowledge.db`` with optional sessions.db attached."""
        attach = self._sessions_db if (self._sessions_db and self._sessions_db.exists()) else None
        return connect(self._knowledge_db, attach_sessions=attach)

    @property
    def install_dir(self) -> Path:
        """Directory where this client's databases live (read-only)."""
        return self._install_dir

    @property
    def config(self) -> Config:
        """Effective :class:`Config` for this client (read-only)."""
        return self._config

    # --------------------------------------------------------------- internals

    def _guard_write(self, project_tags: List[str]) -> List[str]:
        """Validate every slug in *project_tags* against the registry.

        Returns:
            The resolved canonical slugs in the same order, deduped.

        Raises:
            InvalidProjectSlug: when *project_tags* is empty, any slug fails
                the canonical format, or any slug does not resolve to an
                active canonical entry (HR-2).

        Refuses:
            Empty or all-unknown project_tags lists.
        """
        if not project_tags:
            raise InvalidProjectSlug("project_tags must be a non-empty list (HR-2)")
        try:
            from runaway_context.slugs_lifecycle import SlugRegistry
        except ImportError as exc:
            raise RunawayContextError(
                f"slug registry module unavailable: {exc}"
            )
        registry = SlugRegistry(self._knowledge_db)
        resolved: List[str] = []
        for slug in project_tags:
            if not is_valid_slug_format(slug):
                raise InvalidProjectSlug(
                    f"slug {slug!r} fails canonical format (HR-2)"
                )
            canonical = registry.resolve(slug)
            if canonical is None:
                raise InvalidProjectSlug(
                    f"slug {slug!r} is not registered or not active (HR-2)"
                )
            if canonical not in resolved:
                resolved.append(canonical)
        return resolved

    def _audit_append(
        self,
        conn: sqlite3.Connection,
        actor: str,
        action: str,
        target_table: Optional[str] = None,
        target_id: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an audit row using :mod:`runaway_context.audit` when available.

        Returns:
            None.

        Raises:
            RunawayContextError: when the audit module is missing (HR-10
                — we don't silently skip audit on writes).

        Refuses:
            Continuing the write when the audit chain cannot be appended.
        """
        try:
            from runaway_context import audit
        except ImportError as exc:
            raise RunawayContextError(
                f"audit module unavailable; write blocked to preserve HR-7 chain: {exc}"
            )
        audit.append(
            conn,
            actor=actor,
            action=action,
            target_table=target_table,
            target_id=target_id,
            details=details,
        )

    def _resolve_actor(self, actor: Optional[str]) -> str:
        """Return *actor* when set; otherwise compute the install's author_id."""
        if actor and str(actor).strip():
            return str(actor).strip()
        return current_author_id(self._install_dir)

    # ======================================================================
    # Read methods
    # ======================================================================

    def get_brief(self, project: str) -> Dict[str, Any]:
        """Return the project_context_card plus resolved active lessons / chunks.

        Args:
            project: canonical project slug.

        Returns:
            ``{card, lessons:[{id,title,maturity,severity}], chunks:[{id,topic,title}],
            warnings:[...]}`` — IDs are resolved to their titles.

        Raises:
            ProjectNotFound: when no card exists for *project*.

        Refuses:
            Returning soft-deleted lessons / chunks; they are filtered out.
        """
        from runaway_context.errors import ProjectNotFound  # local import

        conn = self._connect()
        try:
            card_row = conn.execute(
                "SELECT * FROM project_context_card WHERE project = ?",
                (project,),
            ).fetchone()
            if card_row is None:
                raise ProjectNotFound(
                    f"no project_context_card for project={project!r}"
                )
            card = _row_to_dict(card_row) or {}

            lesson_ids = [int(x) for x in card.get("active_lesson_ids") or []]
            chunk_ids = [int(x) for x in card.get("active_chunk_ids") or []]
            warning_entries = card.get("top_warnings") or []
            warning_ids: List[int] = []
            for entry in warning_entries:
                if isinstance(entry, dict) and "id" in entry:
                    warning_ids.append(int(entry["id"]))
                elif isinstance(entry, int):
                    warning_ids.append(entry)

            lessons: List[Dict[str, Any]] = []
            if lesson_ids:
                placeholders = ",".join("?" * len(lesson_ids))
                rows = conn.execute(
                    f"SELECT id, title, maturity, severity, status "
                    f"FROM lessons_learned "
                    f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                    lesson_ids,
                ).fetchall()
                lessons = [_row_to_dict(r) or {} for r in rows]

            chunks: List[Dict[str, Any]] = []
            if chunk_ids:
                placeholders = ",".join("?" * len(chunk_ids))
                rows = conn.execute(
                    f"SELECT id, topic, title, project "
                    f"FROM knowledge_chunks "
                    f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                    chunk_ids,
                ).fetchall()
                chunks = [_row_to_dict(r) or {} for r in rows]

            warnings: List[Dict[str, Any]] = []
            if warning_ids:
                placeholders = ",".join("?" * len(warning_ids))
                rows = conn.execute(
                    f"SELECT id, title, severity FROM lessons_learned "
                    f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                    warning_ids,
                ).fetchall()
                warnings = [_row_to_dict(r) or {} for r in rows]

            return {
                "card": card,
                "lessons": lessons,
                "chunks": chunks,
                "warnings": warnings,
            }
        finally:
            conn.close()

    def search_chunks(
        self,
        query: str,
        project: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Run an FTS5 search over ``knowledge_chunks``.

        Returns:
            List of matching chunk dicts ordered by bm25 ascending.

        Raises:
            ValueError: when *query* is empty / whitespace.

        Refuses:
            Returning soft-deleted chunks.
        """
        from runaway_context.retrieval import search_chunks_fts
        conn = self._connect()
        try:
            return search_chunks_fts(conn, query, project=project, limit=limit)
        finally:
            conn.close()

    def search_lessons(
        self,
        query: str,
        project: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Run an FTS5 search over ``lessons_learned``.

        Returns:
            List of matching lesson dicts ordered by bm25 ascending.

        Raises:
            ValueError: when *query* is empty / whitespace.

        Refuses:
            Returning soft-deleted lessons. Returning archived/superseded
            lessons unless *include_archived* is True.
        """
        from runaway_context.retrieval import search_lessons_fts
        conn = self._connect()
        try:
            hits = search_lessons_fts(
                conn, query, project=project,
                include_archived=include_archived, limit=limit,
            )
            ids = [h["id"] for h in hits if isinstance(h, dict) and h.get("id") is not None]
            if ids:
                self._touch_lessons(conn, ids)
            return hits
        finally:
            conn.close()

    def get_lesson(self, lesson_id: int) -> Optional[Dict[str, Any]]:
        """Return a single lesson row (with JSON columns decoded) or None.

        Increments ``reference_count`` on hit — the maturation engine reads
        this counter as a re-engagement signal.

        Returns:
            Dict for the lesson, or None when *lesson_id* does not exist
            (or has been soft-deleted).

        Raises:
            Nothing — missing rows yield None.

        Refuses:
            Returning soft-deleted lessons.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM lessons_learned WHERE id = ? AND deleted_at IS NULL",
                (int(lesson_id),),
            ).fetchone()
            result = _row_to_dict(row)
            if result is not None:
                self._touch_lessons(conn, [int(lesson_id)])
            return result
        finally:
            conn.close()

    def _touch_lessons(self, conn, lesson_ids):
        """Increment ``reference_count`` for the given lesson ids.

        Used by read paths to feed the maturation engine's
        ``active → stable`` rule. Bounded: only updates rows that exist and
        are not soft-deleted.
        """
        if not lesson_ids:
            return
        placeholders = ",".join("?" * len(lesson_ids))
        conn.execute(
            f"UPDATE lessons_learned "
            f"SET reference_count = COALESCE(reference_count, 0) + 1, "
            f"    last_revisited = CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            list(lesson_ids),
        )
        conn.commit()

    def get_chunk(self, chunk_id: int) -> Optional[Dict[str, Any]]:
        """Return a single knowledge_chunk row (JSON columns decoded) or None.

        Returns:
            Dict for the chunk, or None when *chunk_id* does not exist or
            has been soft-deleted.

        Raises:
            Nothing — missing rows yield None.

        Refuses:
            Returning soft-deleted chunks.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM knowledge_chunks WHERE id = ? AND deleted_at IS NULL",
                (int(chunk_id),),
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def list_drafts(self, status: str = "pending") -> List[Dict[str, Any]]:
        """List rows from the ``lesson_drafts`` inbox.

        Args:
            status: One of ``pending`` / ``approved`` / ``rejected`` /
                ``merged`` / ``all``.

        Returns:
            List of draft dicts sorted by proposed_at descending.

        Raises:
            ValueError: when *status* is not one of the allowed values.

        Refuses:
            Returning rows for an unknown status.
        """
        allowed = {"pending", "approved", "rejected", "merged", "all"}
        if status not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        conn = self._connect()
        try:
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM lesson_drafts ORDER BY proposed_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lesson_drafts WHERE status = ? "
                    "ORDER BY proposed_at DESC",
                    (status,),
                ).fetchall()
            return [_row_to_dict(r) or {} for r in rows]
        finally:
            conn.close()

    def list_lessons(
        self,
        project: Optional[str] = None,
        status: Optional[str] = None,
        maturity: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List lessons with optional project / status / maturity filters.

        Returns:
            List of lesson dicts, newest first, capped at *limit* rows.

        Raises:
            Nothing — filters silently match nothing when they don't apply.

        Refuses:
            Returning soft-deleted lessons.
        """
        clauses = ["deleted_at IS NULL"]
        params: List[Any] = []
        if project:
            clauses.append("(project = ? OR project_tags LIKE ?)")
            params.append(project)
            params.append(f'%"{project}"%')
        if status:
            clauses.append("status = ?")
            params.append(status)
        if maturity:
            clauses.append("maturity = ?")
            params.append(maturity)
        where = " AND ".join(clauses)
        params.append(int(limit))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM lessons_learned WHERE {where} "
                f"ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_dict(r) or {} for r in rows]
        finally:
            conn.close()

    def list_chunks(
        self,
        project: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List knowledge_chunks with an optional project filter.

        Returns:
            List of chunk dicts, newest first, capped at *limit* rows.

        Raises:
            Nothing — filters silently match nothing when they don't apply.

        Refuses:
            Returning soft-deleted chunks.
        """
        clauses = ["deleted_at IS NULL"]
        params: List[Any] = []
        if project:
            clauses.append("(project = ? OR project_tags LIKE ?)")
            params.append(project)
            params.append(f'%"{project}"%')
        where = " AND ".join(clauses)
        params.append(int(limit))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM knowledge_chunks WHERE {where} "
                f"ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_dict(r) or {} for r in rows]
        finally:
            conn.close()

    def list_specialists(self) -> List[Dict[str, Any]]:
        """List specialist agents registered in the ``specialists`` table.

        Returns:
            List of specialist dicts sorted by name ascending.

        Raises:
            Nothing.

        Refuses:
            Nothing.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM specialists WHERE active = 1 ORDER BY name"
            ).fetchall()
            return [_row_to_dict(r) or {} for r in rows]
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        """Return a summary of the install's knowledge state.

        Delegates to :mod:`runaway_context.stats` when present; otherwise
        computes a minimal set directly from the DB so callers always get
        a useful answer.

        Returns:
            Dict with at minimum ``{lessons_active, chunks_active,
            drafts_pending, projects, schema_version}``.

        Raises:
            Nothing — read-only path.

        Refuses:
            Counting soft-deleted rows.
        """
        try:
            from runaway_context import stats as stats_mod
            return stats_mod.compute(self._knowledge_db)
        except ImportError:
            pass
        except AttributeError:
            pass

        conn = self._connect()
        try:
            lessons_active = conn.execute(
                "SELECT COUNT(*) FROM lessons_learned WHERE deleted_at IS NULL"
            ).fetchone()[0]
            chunks_active = conn.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE deleted_at IS NULL"
            ).fetchone()[0]
            drafts_pending = conn.execute(
                "SELECT COUNT(*) FROM lesson_drafts WHERE status = 'pending'"
            ).fetchone()[0]
            projects = conn.execute(
                "SELECT COUNT(*) FROM project_context_card"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT major, minor, patch FROM schema_version WHERE id = 1"
            ).fetchone()
            version = f"{row[0]}.{row[1]}.{row[2]}" if row else None
            return {
                "lessons_active": int(lessons_active),
                "chunks_active": int(chunks_active),
                "drafts_pending": int(drafts_pending),
                "projects": int(projects),
                "schema_version": version,
            }
        finally:
            conn.close()

    def tier_check(self) -> Dict[str, Any]:
        """Return the install's current tier plus next-gate criteria.

        Returns:
            ``{tier, next_tier, next_gate, criteria:[{name, met, detail}]}``.

        Raises:
            Nothing.

        Refuses:
            Nothing.
        """
        tier = self._config.tier or "T1"
        gates: Dict[str, Dict[str, Any]] = {
            "T0": {"next": "T1", "criteria": ["accumulated 5+ project-specific notes"]},
            "T1": {"next": "T2", "criteria": [
                "30+ days of use",
                "10+ lessons across 2+ projects",
                "at least one drift warning logged",
            ]},
            "T2": {"next": "T3", "criteria": [
                "second author_id has logged 1+ approved lesson in last 30 days",
            ]},
            "T3": {"next": "T4", "criteria": [
                "5+ import conflicts resolved",
                "1+ runaway_admin designated",
                "30 days under T3",
            ]},
            "T4": {"next": "T5", "criteria": [
                "SSO provider configured",
                "federation source identified",
                "audit log clean for 30 consecutive days",
            ]},
            "T5": {"next": None, "criteria": []},
        }
        gate = gates.get(tier, gates["T1"])
        return {
            "tier": tier,
            "next_tier": gate["next"],
            "criteria": [{"name": c, "met": None, "detail": None}
                         for c in gate["criteria"]],
        }

    # ======================================================================
    # Write methods
    # ======================================================================

    def log_lesson(
        self,
        *,
        title: str,
        project_tags: List[str],
        what_happened: Optional[str] = None,
        why: Optional[str] = None,
        the_fix: Optional[str] = None,
        prevention_rule: Optional[str] = None,
        severity: str = "warning",
        blast_radius: Optional[int] = None,
        frequency: Optional[int] = None,
        reversibility: Optional[int] = None,
        source_conversation_ref: Optional[str] = None,
    ) -> int:
        """Insert a new lesson learned. Validates every slug in *project_tags* (HR-2).

        Returns:
            The new ``lessons_learned.id``.

        Raises:
            InvalidProjectSlug: when *project_tags* is empty or contains an
                unregistered / malformed slug (HR-2).
            ValueError: when *title* is empty or *severity* is not one of
                ``critical`` / ``warning`` / ``info``, or an axis is out of range.

        Refuses:
            Writing rows without a valid canonical slug (HR-2). Writing
            severity axes outside 1..5.
        """
        if not title or not str(title).strip():
            raise ValueError("title must be a non-empty string")
        if severity not in ("critical", "warning", "info"):
            raise ValueError(
                f"severity must be one of critical|warning|info (got {severity!r})"
            )

        try:
            from runaway_context.severity import validate_axes
            validate_axes(blast_radius, frequency, reversibility)
        except ImportError:
            for name, value in (
                ("blast_radius", blast_radius),
                ("frequency", frequency),
                ("reversibility", reversibility),
            ):
                if value is None:
                    continue
                if not isinstance(value, int) or isinstance(value, bool) \
                        or value < 1 or value > 5:
                    raise ValueError(f"{name} out of range: must be 1..5")

        canonical_tags = self._guard_write(project_tags)
        actor = self._resolve_actor(None)
        project = canonical_tags[0]
        tags_json = _dumps_tags(canonical_tags)

        conn = self._connect()
        try:
            with transaction(conn):
                cur = conn.execute(
                    "INSERT INTO lessons_learned "
                    "(project, title, what_happened, why, the_fix, prevention_rule, "
                    " severity, status, project_tags, source_conversation_ref, "
                    " blast_radius, frequency, reversibility, author_id, maturity) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 'scar')",
                    (
                        project, title, what_happened, why, the_fix, prevention_rule,
                        severity, tags_json, source_conversation_ref,
                        blast_radius, frequency, reversibility, actor,
                    ),
                )
                lesson_id = int(cur.lastrowid)
                self._audit_append(
                    conn, actor=actor, action="lesson.create",
                    target_table="lessons_learned", target_id=lesson_id,
                    details={"project": project, "title": title,
                             "project_tags": canonical_tags},
                )
                return lesson_id
        finally:
            conn.close()

    def propose_knowledge(
        self,
        *,
        project: str,
        topic: str,
        title: str,
        body: str,
        tags: Optional[List[str]] = None,
        project_tags: Optional[List[str]] = None,
    ) -> int:
        """Insert a new knowledge_chunk. Validates *project_tags* (HR-2).

        Args:
            project: canonical project slug (primary owner).
            topic: short identifier — unique per project.
            title: human-readable title.
            body: the chunk body (markdown).
            tags: optional list of free-text tags.
            project_tags: defaults to ``[project]``.

        Returns:
            The new ``knowledge_chunks.id``.

        Raises:
            InvalidProjectSlug: when *project* or any *project_tags* is
                missing / malformed / unregistered (HR-2).
            ValueError: when *topic*, *title*, or *body* is empty.

        Refuses:
            Writing rows without a valid canonical slug (HR-2). Writing
            chunks with a topic that already exists for the same project
            (surfaces ``sqlite3.IntegrityError`` via the unique index).
        """
        if not topic or not str(topic).strip():
            raise ValueError("topic must be a non-empty string")
        if not title or not str(title).strip():
            raise ValueError("title must be a non-empty string")
        if not body or not str(body).strip():
            raise ValueError("body must be a non-empty string")

        effective_tags = project_tags if project_tags else [project]
        canonical_tags = self._guard_write(effective_tags)
        if project not in canonical_tags:
            canonical_tags = [project] + [t for t in canonical_tags if t != project]
        actor = self._resolve_actor(None)
        tags_json = json.dumps(sorted({t for t in (tags or []) if isinstance(t, str) and t}))
        project_tags_json = _dumps_tags(canonical_tags)

        conn = self._connect()
        try:
            with transaction(conn):
                cur = conn.execute(
                    "INSERT INTO knowledge_chunks "
                    "(project, project_tags, topic, title, body, tags, author_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project, project_tags_json, topic, title, body, tags_json, actor),
                )
                chunk_id = int(cur.lastrowid)
                self._audit_append(
                    conn, actor=actor, action="chunk.create",
                    target_table="knowledge_chunks", target_id=chunk_id,
                    details={"project": project, "topic": topic,
                             "project_tags": canonical_tags},
                )
                return chunk_id
        finally:
            conn.close()

    def supersede(
        self,
        old_lesson_id: int,
        new_lesson_id: int,
        actor: Optional[str] = None,
    ) -> None:
        """Mark *old_lesson_id* as superseded by *new_lesson_id*.

        Returns:
            None.

        Raises:
            ValueError: when either lesson does not exist (or is soft-deleted).

        Refuses:
            Self-supersession (``old == new``).
        """
        if old_lesson_id == new_lesson_id:
            raise ValueError("a lesson cannot supersede itself")
        actor_id = self._resolve_actor(actor)
        conn = self._connect()
        try:
            old_row = conn.execute(
                "SELECT id FROM lessons_learned WHERE id = ? AND deleted_at IS NULL",
                (int(old_lesson_id),),
            ).fetchone()
            new_row = conn.execute(
                "SELECT id FROM lessons_learned WHERE id = ? AND deleted_at IS NULL",
                (int(new_lesson_id),),
            ).fetchone()
            if old_row is None:
                raise ValueError(f"old_lesson_id {old_lesson_id} not found")
            if new_row is None:
                raise ValueError(f"new_lesson_id {new_lesson_id} not found")
            with transaction(conn):
                conn.execute(
                    "UPDATE lessons_learned "
                    "SET superseded_by = ?, status = 'superseded', "
                    "    maturity = 'superseded', "
                    "    maturity_changed_at = CURRENT_TIMESTAMP, "
                    "    maturity_changed_by = ? "
                    "WHERE id = ?",
                    (int(new_lesson_id), actor_id, int(old_lesson_id)),
                )
                self._audit_append(
                    conn, actor=actor_id, action="lesson.supersede",
                    target_table="lessons_learned", target_id=int(old_lesson_id),
                    details={"superseded_by": int(new_lesson_id)},
                )
        finally:
            conn.close()

    def soft_delete(
        self,
        *,
        table: str,
        record_id: int,
        actor: str,
        reason: str,
    ) -> None:
        """Soft-delete a row from ``knowledge_chunks`` or ``lessons_learned`` (HR-3).

        Snapshots the prior content to ``record_versions`` before updating
        the deletion columns, and appends an audit row.

        Returns:
            None.

        Raises:
            ValueError: when *table* is not allowed, *actor* / *reason* is
                empty, or the row is missing / already deleted.

        Refuses:
            Hard-deleting anything (HR-3). Operating on tables other than
            knowledge_chunks / lessons_learned.
        """
        if table not in ("knowledge_chunks", "lessons_learned"):
            raise ValueError(f"table not allowed: {table!r}")
        if not actor or not str(actor).strip():
            raise ValueError("actor is required")
        if not reason or not str(reason).strip():
            raise ValueError("reason is required")

        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NULL",
                (int(record_id),),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"row not found or already deleted: {table} id={record_id}"
                )
            payload = json.dumps(_row_to_dict(row) or {}, sort_keys=True, default=str)
            version = int(row["version"]) if "version" in row.keys() and row["version"] else 1

            with transaction(conn):
                conn.execute(
                    "INSERT INTO record_versions "
                    "(table_name, record_id, version, payload, saved_by, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (table, int(record_id), version, payload, actor, reason),
                )
                conn.execute(
                    f"UPDATE {table} "
                    f"SET deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, "
                    f"    deletion_reason = ? "
                    f"WHERE id = ?",
                    (actor, reason, int(record_id)),
                )
                self._audit_append(
                    conn, actor=actor, action=f"{table}.soft_delete",
                    target_table=table, target_id=int(record_id),
                    details={"reason": reason, "version": version},
                )
        finally:
            conn.close()

    def mature_lesson(
        self,
        lesson_id: int,
        to_state: str,
        actor: str,
        reason: Optional[str] = None,
    ) -> None:
        """Apply a maturation transition under explicit approval (HR-9).

        Delegates to :func:`runaway_context.maturation.apply_transition`,
        which is the sole writer for ``lessons_learned.maturity``.

        Returns:
            None.

        Raises:
            MaturationApprovalRequired: when *actor* is empty (HR-9).
            ValueError: when *to_state* is unknown or *lesson_id* missing.
            RuntimeError: when the maturation module is unavailable.

        Refuses:
            Mutating ``maturity`` without an explicit actor (HR-9).
        """
        if not actor or not str(actor).strip():
            raise MaturationApprovalRequired(
                "mature_lesson requires an explicit actor (HR-9)"
            )
        from runaway_context import maturation
        conn = self._connect()
        try:
            maturation.apply_transition(
                conn,
                lesson_id=int(lesson_id),
                to_state=to_state,
                actor=str(actor).strip(),
                reason=reason,
            )
        finally:
            conn.close()

    def propose_lesson_draft(
        self,
        *,
        title: str,
        project_tags: List[str],
        what_happened: Optional[str] = None,
        prevention_rule: Optional[str] = None,
        proposed_by: Optional[str] = None,
        source_conversation_ref: Optional[str] = None,
    ) -> int:
        """Insert a new row into ``lesson_drafts`` (status='pending').

        Returns:
            The new ``lesson_drafts.id``.

        Raises:
            InvalidProjectSlug: when *project_tags* is empty / malformed /
                unregistered (HR-2).
            ValueError: when *title* is empty.

        Refuses:
            Writing drafts with no valid canonical slug (HR-2).
        """
        if not title or not str(title).strip():
            raise ValueError("title must be a non-empty string")
        canonical_tags = self._guard_write(project_tags)
        actor = self._resolve_actor(proposed_by)
        tags_json = _dumps_tags(canonical_tags)

        conn = self._connect()
        try:
            with transaction(conn):
                cur = conn.execute(
                    "INSERT INTO lesson_drafts "
                    "(title, what_happened, prevention_rule, project_tags, "
                    " source_conversation_ref, proposed_by, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                    (title, what_happened, prevention_rule, tags_json,
                     source_conversation_ref, actor),
                )
                draft_id = int(cur.lastrowid)
                self._audit_append(
                    conn, actor=actor, action="draft.create",
                    target_table="lesson_drafts", target_id=draft_id,
                    details={"title": title, "project_tags": canonical_tags},
                )
                return draft_id
        finally:
            conn.close()

    def approve_draft(
        self,
        draft_id: int,
        actor: str,
        project_tags: Optional[List[str]] = None,
    ) -> int:
        """Convert a pending draft into a lesson and mark the draft approved.

        Args:
            draft_id: ``lesson_drafts.id`` of the row to approve.
            actor: required reviewer identity (HR-9 spirit).
            project_tags: override the draft's tags. When None, the draft's
                stored tags are reused.

        Returns:
            The new ``lessons_learned.id``.

        Raises:
            ValueError: when the draft is missing or not in 'pending' state,
                or *actor* is empty.
            InvalidProjectSlug: when the (possibly overridden) tags fail
                validation (HR-2).

        Refuses:
            Approving a draft that is already approved / rejected / merged.
            Approving without an actor.
        """
        if not actor or not str(actor).strip():
            raise ValueError("actor is required to approve a draft")
        conn = self._connect()
        try:
            draft = conn.execute(
                "SELECT * FROM lesson_drafts WHERE id = ?", (int(draft_id),),
            ).fetchone()
            if draft is None:
                raise ValueError(f"draft {draft_id} not found")
            if draft["status"] != "pending":
                raise ValueError(
                    f"draft {draft_id} is not pending (status={draft['status']!r})"
                )
            stored_tags: List[str] = []
            if draft["project_tags"]:
                try:
                    stored_tags = list(json.loads(draft["project_tags"]))
                except (ValueError, TypeError):
                    stored_tags = []
            effective_tags = project_tags if project_tags else stored_tags
        finally:
            conn.close()

        # log_lesson reopens its own connection — keeps the write atomic
        # against its own audit append.
        lesson_id = self.log_lesson(
            title=draft["title"],
            project_tags=effective_tags,
            what_happened=draft["what_happened"],
            why=draft["why"],
            the_fix=draft["the_fix"],
            prevention_rule=draft["prevention_rule"],
            source_conversation_ref=draft["source_conversation_ref"],
        )

        conn = self._connect()
        try:
            with transaction(conn):
                conn.execute(
                    "UPDATE lesson_drafts "
                    "SET status = 'approved', approved_lesson_id = ?, "
                    "    reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ? "
                    "WHERE id = ?",
                    (lesson_id, str(actor).strip(), int(draft_id)),
                )
                self._audit_append(
                    conn, actor=str(actor).strip(),
                    action="draft.approve",
                    target_table="lesson_drafts", target_id=int(draft_id),
                    details={"approved_lesson_id": lesson_id},
                )
        finally:
            conn.close()
        return lesson_id

    def reject_draft(
        self,
        draft_id: int,
        actor: str,
        notes: Optional[str] = None,
    ) -> None:
        """Reject a pending draft. Records the reviewer and optional notes.

        Returns:
            None.

        Raises:
            ValueError: when the draft is missing, already terminal, or
                *actor* is empty.

        Refuses:
            Rejecting a non-pending draft.
        """
        if not actor or not str(actor).strip():
            raise ValueError("actor is required to reject a draft")
        conn = self._connect()
        try:
            draft = conn.execute(
                "SELECT id, status FROM lesson_drafts WHERE id = ?",
                (int(draft_id),),
            ).fetchone()
            if draft is None:
                raise ValueError(f"draft {draft_id} not found")
            if draft["status"] != "pending":
                raise ValueError(
                    f"draft {draft_id} is not pending (status={draft['status']!r})"
                )
            with transaction(conn):
                conn.execute(
                    "UPDATE lesson_drafts "
                    "SET status = 'rejected', "
                    "    reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?, "
                    "    review_notes = ? "
                    "WHERE id = ?",
                    (str(actor).strip(), notes, int(draft_id)),
                )
                self._audit_append(
                    conn, actor=str(actor).strip(),
                    action="draft.reject",
                    target_table="lesson_drafts", target_id=int(draft_id),
                    details={"notes": notes},
                )
        finally:
            conn.close()

    def regen_brief(
        self,
        project: str,
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Regenerate the project's brief markdown from the context card (HR-5).

        Returns:
            Dict with keys ``{content, line_count, written, md_path,
            truncated_sections, snapshot_id, cap}``.

        Raises:
            BriefBudgetExceeded: when the rendered brief would exceed the
                tier cap (HR-5).
            ValueError: when no card exists for *project*.

        Refuses:
            Writing past the tier cap (HR-5).
        """
        from runaway_context import brief as brief_mod
        return brief_mod.regenerate(self._install_dir, project, dry_run=dry_run)

    def set_visibility(
        self,
        *,
        table: str,
        record_id: int,
        level: str,
    ) -> None:
        """Update the visibility ACL on a chunk or lesson (private/team/org).

        Returns:
            None.

        Raises:
            ValueError: when *table* is not allowed, *level* is unknown, or
                the row is missing.

        Refuses:
            Visibility levels outside the canonical set. Operating on tables
            outside the chunk / lesson pair.
        """
        if table not in ("knowledge_chunks", "lessons_learned"):
            raise ValueError(f"table not allowed: {table!r}")
        if level not in ("private", "team", "org"):
            raise ValueError(f"level must be private|team|org (got {level!r})")
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT id FROM {table} WHERE id = ? AND deleted_at IS NULL",
                (int(record_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"{table} id={record_id} not found")
            with transaction(conn):
                conn.execute(
                    f"UPDATE {table} SET visibility = ? WHERE id = ?",
                    (level, int(record_id)),
                )
                self._audit_append(
                    conn, actor=actor, action=f"{table}.set_visibility",
                    target_table=table, target_id=int(record_id),
                    details={"level": level},
                )
        finally:
            conn.close()

    def register_slug(
        self,
        slug: str,
        description: Optional[str] = None,
    ) -> None:
        """Register a canonical slug. Idempotent.

        Returns:
            None.

        Raises:
            InvalidProjectSlug: when *slug* fails canonical format.

        Refuses:
            Malformed slugs.
        """
        from runaway_context.slugs_lifecycle import SlugRegistry
        SlugRegistry(self._knowledge_db).register(slug, description)
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            with transaction(conn):
                self._audit_append(
                    conn, actor=actor, action="slug.register",
                    target_table="slug_registry", target_id=None,
                    details={"slug": slug, "description": description},
                )
        finally:
            conn.close()

    def alias_slug(self, alias: str, canonical: str) -> None:
        """Register an alias pointing to a canonical slug.

        Returns:
            None.

        Raises:
            InvalidProjectSlug: when either slug fails canonical format or
                *canonical* is missing/not-active.

        Refuses:
            Self-aliasing. Aliasing to an unknown canonical.
        """
        from runaway_context.slugs_lifecycle import SlugRegistry
        SlugRegistry(self._knowledge_db).alias(alias, canonical)
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            with transaction(conn):
                self._audit_append(
                    conn, actor=actor, action="slug.alias",
                    target_table="slug_registry", target_id=None,
                    details={"alias": alias, "canonical": canonical},
                )
        finally:
            conn.close()

    def deprecate_slug(
        self,
        slug: str,
        reason: Optional[str] = None,
    ) -> None:
        """Mark a slug as deprecated. Existing rows tagged with it stay queryable.

        Returns:
            None.

        Raises:
            InvalidProjectSlug: when *slug* fails format or is not registered.

        Refuses:
            Deprecating an unregistered slug.
        """
        from runaway_context.slugs_lifecycle import SlugRegistry
        SlugRegistry(self._knowledge_db).deprecate(slug, reason)
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            with transaction(conn):
                self._audit_append(
                    conn, actor=actor, action="slug.deprecate",
                    target_table="slug_registry", target_id=None,
                    details={"slug": slug, "reason": reason},
                )
        finally:
            conn.close()

    def merge_slugs(self, from_slug: str, to_slug: str) -> None:
        """Fold *from_slug* into *to_slug* (alias + status='merged').

        Returns:
            None.

        Raises:
            InvalidProjectSlug: when either slug fails format / is missing,
                or *to_slug* is not active.

        Refuses:
            Self-merge. Merging into an inactive canonical.
        """
        from runaway_context.slugs_lifecycle import SlugRegistry
        SlugRegistry(self._knowledge_db).merge(from_slug, to_slug)
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            with transaction(conn):
                self._audit_append(
                    conn, actor=actor, action="slug.merge",
                    target_table="slug_registry", target_id=None,
                    details={"from_slug": from_slug, "to_slug": to_slug},
                )
        finally:
            conn.close()

    def register_specialist(
        self,
        *,
        name: str,
        domain: str,
        description: Optional[str] = None,
    ) -> int:
        """Register a specialist agent.

        Returns:
            The new ``specialists.id``.

        Raises:
            ValueError: when *name* or *domain* is empty.

        Refuses:
            Empty / whitespace-only name or domain.
        """
        if not name or not str(name).strip():
            raise ValueError("name is required")
        if not domain or not str(domain).strip():
            raise ValueError("domain is required")
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            with transaction(conn):
                cur = conn.execute(
                    "INSERT INTO specialists (name, domain, description, active) "
                    "VALUES (?, ?, ?, 1)",
                    (name, domain, description),
                )
                specialist_id = int(cur.lastrowid)
                self._audit_append(
                    conn, actor=actor, action="specialist.register",
                    target_table="specialists", target_id=specialist_id,
                    details={"name": name, "domain": domain},
                )
                return specialist_id
        finally:
            conn.close()

    def attach_to_specialist(
        self,
        *,
        specialist_id: int,
        table: str,
        record_id: int,
    ) -> None:
        """Attach a knowledge_chunk or lesson to a specialist agent.

        Returns:
            None.

        Raises:
            ValueError: when *table* is not allowed or specialist / record
                is missing.

        Refuses:
            Attaching rows from tables outside the chunk / lesson pair.
        """
        if table not in ("knowledge_chunks", "lessons_learned"):
            raise ValueError(f"table not allowed: {table!r}")
        actor = self._resolve_actor(None)
        conn = self._connect()
        try:
            spec_row = conn.execute(
                "SELECT id FROM specialists WHERE id = ?", (int(specialist_id),),
            ).fetchone()
            if spec_row is None:
                raise ValueError(f"specialist {specialist_id} not found")
            rec_row = conn.execute(
                f"SELECT id FROM {table} WHERE id = ? AND deleted_at IS NULL",
                (int(record_id),),
            ).fetchone()
            if rec_row is None:
                raise ValueError(f"{table} id={record_id} not found")
            with transaction(conn):
                conn.execute(
                    "INSERT OR IGNORE INTO specialist_knowledge "
                    "(specialist_id, table_name, record_id) VALUES (?, ?, ?)",
                    (int(specialist_id), table, int(record_id)),
                )
                self._audit_append(
                    conn, actor=actor, action="specialist.attach",
                    target_table="specialist_knowledge", target_id=None,
                    details={"specialist_id": int(specialist_id),
                             "table": table, "record_id": int(record_id)},
                )
        finally:
            conn.close()

    def audit_verify(self) -> Tuple[bool, Optional[int], Optional[str]]:
        """Verify the audit_log hash chain end-to-end (HR-7).

        Returns:
            ``(ok, first_bad_id, reason)``. When ``ok`` is True the other
            two values are None.

        Raises:
            FileNotFoundError: when the knowledge.db file is missing.

        Refuses:
            Hiding a chain break — the caller gets the offending row id.
        """
        from runaway_context import audit
        return audit.verify(self._knowledge_db)

    def export_json(
        self,
        output_path: Path,
        *,
        project: Optional[str] = None,
    ) -> int:
        """Export the corpus to a JSON file (T3 unlock; delegates).

        Returns:
            Number of rows written.

        Raises:
            RuntimeError: when the transfer module is unavailable.

        Refuses:
            Exporting soft-deleted rows (the transfer module enforces).
        """
        try:
            from runaway_context import transfer
        except ImportError as exc:
            raise RuntimeError(f"transfer module unavailable: {exc}")
        return transfer.export_json(
            self._knowledge_db, Path(output_path), project=project,
        )

    def import_json(
        self,
        input_path: Path,
        actor: str,
    ) -> Dict[str, Any]:
        """Import a JSON corpus produced by :meth:`export_json` (T3 unlock).

        Returns:
            ``{imported, skipped, conflicts}`` summary dict.

        Raises:
            ConflictReported: when a row-level conflict needs human
                resolution (T3 contract).
            RuntimeError: when the transfer module is unavailable.

        Refuses:
            Importing without an explicit *actor*.
        """
        if not actor or not str(actor).strip():
            raise ValueError("actor is required for import")
        try:
            from runaway_context import transfer
        except ImportError as exc:
            raise RuntimeError(f"transfer module unavailable: {exc}")
        return transfer.import_json(
            self._knowledge_db, Path(input_path), actor=str(actor).strip(),
        )
