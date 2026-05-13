"""Minimal MCP server (E11) — stdio transport, 13 tools, 3 resources.

MCP is the Model Context Protocol. The wire format is JSON-RPC 2.0. Two
stdio framing modes are supported:

1. **Content-Length** (``FRAMING_CONTENT_LENGTH``, default) — the
   spec-correct framing used by the official MCP SDKs. Each message is
   preceded by ``Content-Length: N\\r\\n\\r\\n`` followed by exactly N
   bytes of UTF-8 JSON body. This is what real MCP clients negotiate.

2. **Newline-delimited JSON** (``FRAMING_NDJSON``, opt-in) — one JSON
   message per line. Convenient for piping by hand (e.g.
   ``echo '{...}' | runaway mcp serve``) and for fixture-driven tests.

The framing mode is resolved at the boundary in :func:`serve_stdio`. The
internal dispatcher and :func:`serve_test` helper are framing-agnostic.
Selection priority:

* explicit ``framing=`` keyword argument, then
* ``RC_MCP_FRAMING`` env var (``content-length`` or ``ndjson``), then
* ``FRAMING_CONTENT_LENGTH`` (default).

HR-1 still applies — both framings are implemented with stdlib only.

Tools (13):
    1. get_brief
    2. search_chunks
    3. search_lessons
    4. propose_lesson_draft       — writes to drafts inbox, NOT lessons_learned (HR-3)
    5. list_drafts
    6. log_lesson                  — HR-2 enforced
    7. propose_knowledge
    8. mature_lesson
    9. regen_brief
   10. audit_verify
   11. stats
   12. tier_check
   13. list_specialists

Resources:
    runaway://brief/<project>   — current brief content
    runaway://stats             — install-wide stats
    runaway://hard-rules        — text of docs/HARD_RULES.md (fallback if missing)

Refuses:
    Any tool call missing a required argument (returns isError:true).
    Any tool that would bypass the recoverable-write contract (HR-3).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from runaway_context.config import Config


_log = logging.getLogger("runaway_context.mcp_server")

# Protocol constants (subset of MCP we implement).
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "runaway-context"
SERVER_VERSION = "3.0.0"

JSONRPC_VERSION = "2.0"

# JSON-RPC error codes (per spec).
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# stdio framing modes.
FRAMING_CONTENT_LENGTH = "content-length"
FRAMING_NDJSON = "ndjson"
_FRAMING_ENV_VAR = "RC_MCP_FRAMING"


def _resolve_framing(framing: Optional[str]) -> str:
    """Resolve the active stdio framing mode.

    Resolution priority: explicit ``framing`` argument, then the
    ``RC_MCP_FRAMING`` environment variable, then the default
    :data:`FRAMING_CONTENT_LENGTH`. Unknown values fall back to the
    default (HR-10: never silent — a warning is logged).

    Returns:
        Either :data:`FRAMING_CONTENT_LENGTH` or :data:`FRAMING_NDJSON`.

    Refuses:
        Nothing — invalid input is logged and coerced to the default.
    """
    candidate = framing
    if candidate is None:
        candidate = os.environ.get(_FRAMING_ENV_VAR)
    if candidate is None or candidate == "":
        return FRAMING_CONTENT_LENGTH
    candidate = candidate.strip().lower()
    if candidate in (FRAMING_CONTENT_LENGTH, FRAMING_NDJSON):
        return candidate
    _log.warning(
        "unknown MCP framing %r — falling back to %s",
        candidate, FRAMING_CONTENT_LENGTH,
    )
    return FRAMING_CONTENT_LENGTH


# ---------------------------------------------------------------- mini-validator

def _validate_input(args: Any, schema: Dict[str, Any]) -> Optional[str]:
    """Validate ``args`` against a tiny JSON Schema subset.

    Supported keywords (only those we use): ``type``, ``properties``,
    ``required``, ``enum``, ``items`` for type=array, ``additionalProperties``
    is ignored (we are permissive on extras, strict on required+types).

    Returns:
        ``None`` when valid, or a string describing the first violation.

    Refuses:
        Schemas that include keywords outside the supported subset are
        treated as "pass" — the validator is best-effort, not a full
        JSON-Schema engine.
    """
    if not isinstance(schema, dict):
        return None
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(args, dict):
            return f"expected object, got {type(args).__name__}"
        required = schema.get("required") or []
        for key in required:
            if key not in args or args[key] is None:
                return f"missing required arg: {key!r}"
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in args and args[key] is not None:
                err = _validate_input(args[key], sub)
                if err is not None:
                    return f"{key}: {err}"
        return None
    if expected_type == "string":
        if not isinstance(args, str):
            return f"expected string, got {type(args).__name__}"
        enum = schema.get("enum")
        if enum is not None and args not in enum:
            return f"value {args!r} not in enum {enum!r}"
        return None
    if expected_type == "integer":
        if isinstance(args, bool) or not isinstance(args, int):
            return f"expected integer, got {type(args).__name__}"
        return None
    if expected_type == "number":
        if isinstance(args, bool) or not isinstance(args, (int, float)):
            return f"expected number, got {type(args).__name__}"
        return None
    if expected_type == "boolean":
        if not isinstance(args, bool):
            return f"expected boolean, got {type(args).__name__}"
        return None
    if expected_type == "array":
        if not isinstance(args, list):
            return f"expected array, got {type(args).__name__}"
        items = schema.get("items")
        if isinstance(items, dict):
            for i, el in enumerate(args):
                err = _validate_input(el, items)
                if err is not None:
                    return f"[{i}]: {err}"
        return None
    return None


# ---------------------------------------------------------------- tool registry

def _project_arg() -> Dict[str, Any]:
    return {"type": "string"}


def _project_tags_arg() -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "get_brief",
        "description": "Return the auto-generated brief for a project.",
        "inputSchema": {
            "type": "object",
            "required": ["project"],
            "properties": {"project": _project_arg()},
        },
    },
    {
        "name": "search_chunks",
        "description": "Full-text search over knowledge_chunks (active rows).",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "project": _project_arg(),
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "search_lessons",
        "description": "Search lessons_learned by title/what/why text.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "project": _project_arg(),
                "include_archived": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "propose_lesson_draft",
        "description": (
            "Append a candidate lesson to the drafts inbox (HR-3: drafts only, "
            "never inserts into lessons_learned directly)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["title", "project_tags"],
            "properties": {
                "title": {"type": "string"},
                "project_tags": _project_tags_arg(),
                "what_happened": {"type": "string"},
                "prevention_rule": {"type": "string"},
                "source_conversation_ref": {"type": "string"},
                "proposed_by": {"type": "string"},
            },
        },
    },
    {
        "name": "list_drafts",
        "description": "List lesson drafts in the inbox by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "approved", "rejected", "merged"],
                },
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "log_lesson",
        "description": "Log a fully-formed lesson (HR-2: project_tags required).",
        "inputSchema": {
            "type": "object",
            "required": ["title", "project_tags"],
            "properties": {
                "title": {"type": "string"},
                "project_tags": _project_tags_arg(),
                "what_happened": {"type": "string"},
                "why": {"type": "string"},
                "the_fix": {"type": "string"},
                "prevention_rule": {"type": "string"},
                "severity": {
                    "type": "string",
                    "enum": ["critical", "warning", "info"],
                },
                "blast_radius": {"type": "integer"},
                "frequency": {"type": "integer"},
                "reversibility": {"type": "integer"},
            },
        },
    },
    {
        "name": "propose_knowledge",
        "description": "Propose a knowledge chunk (HR-2: project required).",
        "inputSchema": {
            "type": "object",
            "required": ["project", "title", "body"],
            "properties": {
                "project": _project_arg(),
                "topic": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "mature_lesson",
        "description": "Apply a maturity transition under explicit approval (HR-9).",
        "inputSchema": {
            "type": "object",
            "required": ["lesson_id", "to_state", "actor"],
            "properties": {
                "lesson_id": {"type": "integer"},
                "to_state": {
                    "type": "string",
                    "enum": ["scar", "active", "stable",
                             "internalized", "superseded", "archived"],
                },
                "actor": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "regen_brief",
        "description": "Regenerate the brief for a project (dry-run supported).",
        "inputSchema": {
            "type": "object",
            "required": ["project"],
            "properties": {
                "project": _project_arg(),
                "dry_run": {"type": "boolean"},
            },
        },
    },
    {
        "name": "audit_verify",
        "description": "Recompute the audit_log hash chain and report status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stats",
        "description": "Top-level counts for chunks, lessons, drafts, snapshots.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tier_check",
        "description": "Report the install's current tier and promotion gate status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_specialists",
        "description": "List registered specialist agents.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

assert len(_TOOLS) == 13, "MCP server must expose exactly 13 tools"


# ---------------------------------------------------------------- helpers

def _load_client(install_dir: Optional[Path] = None) -> Any:
    """Lazily import and instantiate ``runaway_context.Client``.

    Returns:
        A Client instance bound to ``install_dir`` (or the default install).

    Refuses:
        When the Client module is not yet on disk (raises ImportError so the
        caller can fall back to direct-SQL handlers — HR-10).
    """
    mod = importlib.import_module("runaway_context.client")
    cls = getattr(mod, "Client", None)
    if cls is None:
        raise ImportError("runaway_context.client.Client not found")
    if install_dir is None:
        return cls()
    return cls(install_dir=install_dir)


def _load_audit() -> Any:
    """Return the runaway_context.audit module if available, else None."""
    try:
        return importlib.import_module("runaway_context.audit")
    except ImportError:
        return None


def _ok(text: str) -> Dict[str, Any]:
    """Build a successful MCP tools/call payload."""
    return {"isError": False, "content": [{"type": "text", "text": text}]}


def _err(text: str) -> Dict[str, Any]:
    """Build a refusal MCP tools/call payload (HR-10: surfaces, not silent)."""
    return {"isError": True, "content": [{"type": "text", "text": text}]}


def _as_json_text(payload: Any) -> str:
    """JSON-encode ``payload`` for the text content channel."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _config(install_dir: Optional[Path]) -> Config:
    return Config.load(install_dir)


def _connect_kdb(install_dir: Optional[Path]) -> sqlite3.Connection:
    """Open knowledge.db read/write via Config defaults."""
    cfg = _config(install_dir)
    if cfg.knowledge_db is None:
        raise RuntimeError("config has no knowledge_db path")
    from runaway_context._db import connect
    return connect(cfg.knowledge_db)


# ---------------------------------------------------------------- tool handlers

def _tool_get_brief(args: Dict[str, Any], install_dir: Optional[Path]) -> Dict[str, Any]:
    """Return the brief content for a project (HR-14)."""
    project = args["project"]
    # Preferred: ask Client.get_brief() if it exists.
    try:
        client = _load_client(install_dir)
        getter = getattr(client, "get_brief", None)
        if callable(getter):
            content = getter(project)
            if isinstance(content, dict):
                return _ok(_as_json_text(content))
            return _ok(str(content))
    except ImportError:
        pass
    # Fallback: read the md file directly.
    from runaway_context.brief_preview import _resolve_md_path  # type: ignore[attr-defined]
    md_path = _resolve_md_path(_config(install_dir).install_dir, project)
    if not md_path.exists():
        return _err(f"no brief found for project={project!r} at {md_path}")
    return _ok(md_path.read_text(encoding="utf-8"))


def _tool_search_chunks(args: Dict[str, Any],
                         install_dir: Optional[Path]) -> Dict[str, Any]:
    """Search active knowledge_chunks by full-text and (optional) project tag."""
    query = args["query"]
    project = args.get("project")
    limit = int(args.get("limit") or 20)
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "search_chunks", None)
        if callable(fn):
            rows = fn(query=query, project=project, limit=limit)
            return _ok(_as_json_text(rows))
    except ImportError:
        pass

    conn = _connect_kdb(install_dir)
    try:
        like = f"%{query}%"
        sql = (
            "SELECT id, project_tags, COALESCE(title, '') AS title, "
            "       COALESCE(body, '') AS body, created_at "
            "FROM knowledge_chunks "
            "WHERE deleted_at IS NULL "
            "  AND (title LIKE ? OR body LIKE ?)"
        )
        params: List[Any] = [like, like]
        if project:
            sql += " AND project_tags LIKE ?"
            params.append(f"%{project}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return _ok(_as_json_text({"matches": rows, "count": len(rows)}))
    finally:
        conn.close()


def _tool_search_lessons(args: Dict[str, Any],
                          install_dir: Optional[Path]) -> Dict[str, Any]:
    """Search lessons_learned by query string."""
    query = args["query"]
    project = args.get("project")
    include_archived = bool(args.get("include_archived"))
    limit = int(args.get("limit") or 20)
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "search_lessons", None)
        if callable(fn):
            rows = fn(query=query, project=project,
                      include_archived=include_archived, limit=limit)
            return _ok(_as_json_text(rows))
    except ImportError:
        pass

    conn = _connect_kdb(install_dir)
    try:
        like = f"%{query}%"
        sql = (
            "SELECT id, project_tags, COALESCE(title, '') AS title, "
            "       COALESCE(what_happened, '') AS what_happened, "
            "       COALESCE(why, '') AS why, maturity, created_at "
            "FROM lessons_learned "
            "WHERE deleted_at IS NULL "
            "  AND (title LIKE ? OR what_happened LIKE ? OR why LIKE ?)"
        )
        params: List[Any] = [like, like, like]
        if not include_archived:
            sql += " AND COALESCE(maturity, 'active') != 'archived'"
        if project:
            sql += " AND project_tags LIKE ?"
            params.append(f"%{project}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return _ok(_as_json_text({"matches": rows, "count": len(rows)}))
    finally:
        conn.close()


def _tool_propose_lesson_draft(args: Dict[str, Any],
                                install_dir: Optional[Path]) -> Dict[str, Any]:
    """Insert a draft into the lesson_drafts inbox. HR-3: drafts, not lessons."""
    title = args["title"]
    project_tags = list(args["project_tags"])
    if not project_tags:
        return _err("project_tags must be non-empty (HR-2)")
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "propose_lesson_draft", None)
        if callable(fn):
            draft_id = fn(
                title=title,
                project_tags=project_tags,
                what_happened=args.get("what_happened"),
                prevention_rule=args.get("prevention_rule"),
                source_conversation_ref=args.get("source_conversation_ref"),
                proposed_by=args.get("proposed_by"),
            )
            return _ok(_as_json_text({"draft_id": draft_id, "status": "pending"}))
    except ImportError:
        pass

    conn = _connect_kdb(install_dir)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO lesson_drafts "
                "(title, what_happened, prevention_rule, project_tags, "
                " source_conversation_ref, proposed_by, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    title,
                    args.get("what_happened"),
                    args.get("prevention_rule"),
                    json.dumps(project_tags),
                    args.get("source_conversation_ref"),
                    args.get("proposed_by"),
                ),
            )
            return _ok(_as_json_text({"draft_id": cur.lastrowid, "status": "pending"}))
    finally:
        conn.close()


def _tool_list_drafts(args: Dict[str, Any],
                       install_dir: Optional[Path]) -> Dict[str, Any]:
    """List draft inbox rows by status."""
    status = args.get("status")
    limit = int(args.get("limit") or 50)
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "list_drafts", None)
        if callable(fn):
            rows = fn(status=status) if status else fn()
            if isinstance(rows, list) and len(rows) > limit:
                rows = rows[:limit]
            return _ok(_as_json_text(rows))
    except ImportError:
        pass
    except (TypeError, ValueError) as exc:
        return _err(f"list_drafts: {exc}")

    conn = _connect_kdb(install_dir)
    try:
        sql = (
            "SELECT id, title, status, project_tags, proposed_by, proposed_at, "
            "       source_conversation_ref "
            "FROM lesson_drafts"
        )
        params: List[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return _ok(_as_json_text({"drafts": rows, "count": len(rows)}))
    finally:
        conn.close()


def _tool_log_lesson(args: Dict[str, Any],
                      install_dir: Optional[Path]) -> Dict[str, Any]:
    """Log a fully-formed lesson via Client.log_lesson (HR-2 enforced there)."""
    project_tags = list(args["project_tags"])
    if not project_tags:
        return _err("project_tags must be non-empty (HR-2)")
    try:
        client = _load_client(install_dir)
    except ImportError:
        return _err(
            "Client not yet available — log_lesson requires runaway_context.Client; "
            "use propose_lesson_draft instead until the Client lands."
        )
    fn = getattr(client, "log_lesson", None)
    if not callable(fn):
        return _err("Client.log_lesson is not implemented in this build")
    payload = {
        "title": args["title"],
        "project_tags": project_tags,
        "what_happened": args.get("what_happened"),
        "why": args.get("why"),
        "the_fix": args.get("the_fix"),
        "prevention_rule": args.get("prevention_rule"),
        "severity": args.get("severity"),
        "blast_radius": args.get("blast_radius"),
        "frequency": args.get("frequency"),
        "reversibility": args.get("reversibility"),
    }
    lesson_id = fn(**{k: v for k, v in payload.items() if v is not None})
    return _ok(_as_json_text({"lesson_id": lesson_id}))


def _tool_propose_knowledge(args: Dict[str, Any],
                             install_dir: Optional[Path]) -> Dict[str, Any]:
    """Propose a knowledge chunk via Client.propose_knowledge (HR-2 enforced)."""
    try:
        client = _load_client(install_dir)
    except ImportError:
        return _err(
            "Client not yet available — propose_knowledge requires "
            "runaway_context.Client."
        )
    fn = getattr(client, "propose_knowledge", None)
    if not callable(fn):
        return _err("Client.propose_knowledge is not implemented in this build")
    chunk_id = fn(
        project=args["project"],
        topic=args.get("topic"),
        title=args["title"],
        body=args["body"],
        tags=list(args.get("tags") or []),
    )
    return _ok(_as_json_text({"chunk_id": chunk_id}))


def _tool_mature_lesson(args: Dict[str, Any],
                         install_dir: Optional[Path]) -> Dict[str, Any]:
    """Apply a maturity transition with explicit approval (HR-9)."""
    lesson_id = int(args["lesson_id"])
    to_state = args["to_state"]
    actor = args["actor"]
    reason = args.get("reason")
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "mature_lesson", None)
        if callable(fn):
            fn(lesson_id=lesson_id, to_state=to_state,
               actor=actor, reason=reason)
            return _ok(_as_json_text({
                "lesson_id": lesson_id, "to_state": to_state, "actor": actor,
            }))
    except ImportError:
        pass

    # Fallback: use the maturation module directly.
    from runaway_context.maturation import apply_transition
    conn = _connect_kdb(install_dir)
    try:
        apply_transition(conn, lesson_id=lesson_id, to_state=to_state,
                         actor=actor, reason=reason)
        return _ok(_as_json_text({
            "lesson_id": lesson_id, "to_state": to_state, "actor": actor,
        }))
    finally:
        conn.close()


def _tool_regen_brief(args: Dict[str, Any],
                       install_dir: Optional[Path]) -> Dict[str, Any]:
    """Regenerate a brief (dry-run supported)."""
    project = args["project"]
    dry_run = bool(args.get("dry_run") or False)
    cfg = _config(install_dir)
    if dry_run:
        from runaway_context.brief_preview import preview
        return _ok(_as_json_text(preview(cfg.install_dir, project)))
    try:
        brief_mod = importlib.import_module("runaway_context.brief")
    except ImportError:
        return _err("runaway_context.brief.regenerate is not yet built")
    fn = getattr(brief_mod, "regenerate", None)
    if not callable(fn):
        return _err("brief module loaded but exposes no regenerate()")
    result = fn(install_dir=cfg.install_dir, project=project, dry_run=False)
    return _ok(_as_json_text(result))


def _tool_audit_verify(args: Dict[str, Any],
                        install_dir: Optional[Path]) -> Dict[str, Any]:
    """Verify the audit_log hash chain (HR-7)."""
    audit_mod = _load_audit()
    if audit_mod is not None and hasattr(audit_mod, "verify"):
        result = audit_mod.verify(_config(install_dir).knowledge_db)
        if isinstance(result, tuple) and len(result) == 3:
            valid, first_bad_id, reason = result
            return _ok(_as_json_text({
                "valid": bool(valid),
                "first_bad_id": first_bad_id,
                "reason": reason,
            }))
        if isinstance(result, dict):
            return _ok(_as_json_text(result))
        return _ok(_as_json_text({"result": result}))

    # Inline fallback verifier — same chain rules used elsewhere.
    import hashlib as _hashlib
    conn = _connect_kdb(install_dir)
    try:
        rows = conn.execute(
            "SELECT id, occurred_at, actor, action, target_id, "
            "       previous_hash, this_hash "
            "FROM audit_log ORDER BY id ASC"
        ).fetchall()
        previous: Optional[str] = None
        for row in rows:
            ts = str(row["occurred_at"]) if row["occurred_at"] is not None else ""
            actor = row["actor"] or ""
            action = row["action"] or ""
            target_id = str(row["target_id"]) if row["target_id"] is not None else ""
            payload = "|".join([previous or "", actor, action, target_id, ts])
            expected = _hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
            if row["this_hash"] != expected or row["previous_hash"] != previous:
                return _ok(_as_json_text({
                    "valid": False,
                    "first_bad_id": int(row["id"]),
                    "reason": "hash mismatch",
                }))
            previous = row["this_hash"]
        return _ok(_as_json_text({
            "valid": True,
            "first_bad_id": None,
            "reason": None,
            "rows_verified": len(rows),
        }))
    finally:
        conn.close()


def _tool_stats(args: Dict[str, Any],
                 install_dir: Optional[Path]) -> Dict[str, Any]:
    """Return top-level install statistics."""
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "stats", None)
        if callable(fn):
            return _ok(_as_json_text(fn()))
    except ImportError:
        pass

    counts: Dict[str, Any] = {}
    conn = _connect_kdb(install_dir)
    try:
        for table in ("knowledge_chunks", "lessons_learned",
                      "lesson_drafts", "brief_snapshots",
                      "audit_log", "specialists", "data_sources"):
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = int(row[0]) if row else 0
            except sqlite3.OperationalError:
                counts[table] = None

        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM lessons_learned "
                "WHERE deleted_at IS NULL"
            ).fetchone()
            counts["lessons_active"] = int(row[0]) if row else 0
        except sqlite3.OperationalError:
            counts["lessons_active"] = None

        try:
            rows = conn.execute(
                "SELECT maturity, COUNT(*) FROM lessons_learned "
                "WHERE deleted_at IS NULL GROUP BY maturity"
            ).fetchall()
            counts["by_maturity"] = {(r[0] or "active"): int(r[1]) for r in rows}
        except sqlite3.OperationalError:
            counts["by_maturity"] = {}
        return _ok(_as_json_text(counts))
    finally:
        conn.close()


def _tool_tier_check(args: Dict[str, Any],
                      install_dir: Optional[Path]) -> Dict[str, Any]:
    """Report current install tier and next promotion gate status."""
    cfg = _config(install_dir)
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "tier_check", None)
        if callable(fn):
            return _ok(_as_json_text(fn()))
    except ImportError:
        pass
    return _ok(_as_json_text({
        "tier": cfg.tier,
        "mcp_enabled": cfg.mcp_enabled,
        "embeddings_enabled": cfg.embeddings_enabled,
        "telemetry_enabled": cfg.telemetry_enabled,
        "next_gate_note": (
            "Tier promotion gates are checked via "
            "`runaway tier promote --to TX --check`."
        ),
    }))


def _tool_list_specialists(args: Dict[str, Any],
                            install_dir: Optional[Path]) -> Dict[str, Any]:
    """List registered specialist agents."""
    try:
        client = _load_client(install_dir)
        fn = getattr(client, "list_specialists", None)
        if callable(fn):
            return _ok(_as_json_text(fn()))
    except ImportError:
        pass

    conn = _connect_kdb(install_dir)
    try:
        rows = conn.execute(
            "SELECT id, name, domain, description, md_path, active, "
            "       created_at, updated_at "
            "FROM specialists "
            "WHERE active = 1 "
            "ORDER BY name"
        ).fetchall()
        return _ok(_as_json_text({
            "specialists": [dict(r) for r in rows],
            "count": len(rows),
        }))
    finally:
        conn.close()


_TOOL_HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_brief": _tool_get_brief,
    "search_chunks": _tool_search_chunks,
    "search_lessons": _tool_search_lessons,
    "propose_lesson_draft": _tool_propose_lesson_draft,
    "list_drafts": _tool_list_drafts,
    "log_lesson": _tool_log_lesson,
    "propose_knowledge": _tool_propose_knowledge,
    "mature_lesson": _tool_mature_lesson,
    "regen_brief": _tool_regen_brief,
    "audit_verify": _tool_audit_verify,
    "stats": _tool_stats,
    "tier_check": _tool_tier_check,
    "list_specialists": _tool_list_specialists,
}
assert set(_TOOL_HANDLERS) == {t["name"] for t in _TOOLS}, \
    "tool handlers and tool schemas must agree"


# ---------------------------------------------------------------- resources

def _resource_brief(uri: str, install_dir: Optional[Path]) -> Dict[str, Any]:
    """Return a brief content resource at runaway://brief/<project>."""
    prefix = "runaway://brief/"
    if not uri.startswith(prefix):
        return {"uri": uri, "mimeType": "text/plain", "text": "(unknown resource)"}
    project = uri[len(prefix):]
    result = _tool_get_brief({"project": project}, install_dir)
    text = "".join(c["text"] for c in result["content"])
    return {"uri": uri, "mimeType": "text/markdown", "text": text}


def _resource_stats(install_dir: Optional[Path]) -> Dict[str, Any]:
    """Return runaway://stats as JSON text."""
    result = _tool_stats({}, install_dir)
    text = "".join(c["text"] for c in result["content"])
    return {"uri": "runaway://stats", "mimeType": "application/json", "text": text}


def _resource_hard_rules(install_dir: Optional[Path]) -> Dict[str, Any]:
    """Return docs/HARD_RULES.md or a short fallback."""
    candidates = [
        Path(__file__).parent.parent.parent / "docs" / "HARD_RULES.md",
        Path("/var/www/html/_sMs/RunawayContext_v3/docs/HARD_RULES.md"),
    ]
    for p in candidates:
        if p.exists():
            return {
                "uri": "runaway://hard-rules",
                "mimeType": "text/markdown",
                "text": p.read_text(encoding="utf-8"),
            }
    fallback = (
        "# RunawayContext Hard Rules (fallback)\n\n"
        "The full HARD_RULES.md file has not been written yet in this checkout. "
        "See Part 0 of the implementation plan for the 15 hard rules.\n"
    )
    return {"uri": "runaway://hard-rules", "mimeType": "text/markdown", "text": fallback}


def _list_resources() -> List[Dict[str, Any]]:
    """Return the static list of MCP resources we advertise."""
    return [
        {
            "uri": "runaway://brief/<project>",
            "name": "Project brief",
            "description": "Current auto-generated CLAUDE.md content for a project.",
            "mimeType": "text/markdown",
        },
        {
            "uri": "runaway://stats",
            "name": "Install stats",
            "description": "Counts across the knowledge store.",
            "mimeType": "application/json",
        },
        {
            "uri": "runaway://hard-rules",
            "name": "Hard rules",
            "description": "The 15 contract rules every implementation honors.",
            "mimeType": "text/markdown",
        },
    ]


# ---------------------------------------------------------------- dispatch

def _build_response(req_id: Any, result: Any = None,
                    error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a JSON-RPC 2.0 response envelope."""
    out: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def _handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ``initialize`` — declare protocol/server info and capabilities."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
        },
    }


def _handle_tools_list() -> Dict[str, Any]:
    """Return the static tool registry."""
    return {"tools": _TOOLS}


def _handle_tools_call(params: Dict[str, Any],
                       install_dir: Optional[Path]) -> Dict[str, Any]:
    """Dispatch a tools/call to the registered handler."""
    name = params.get("name")
    if not isinstance(name, str):
        return _err("tools/call: missing string 'name'")
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _err(f"tools/call: unknown tool {name!r}")
    schema = next(t["inputSchema"] for t in _TOOLS if t["name"] == name)
    arguments = params.get("arguments") or {}
    err = _validate_input(arguments, schema)
    if err is not None:
        return _err(f"tools/call({name}): invalid arguments: {err}")
    try:
        return handler(arguments, install_dir)
    except Exception as exc:  # surface every failure (HR-10)
        _log.exception("tool %s failed", name)
        return _err(f"{type(exc).__name__}: {exc}")


def _handle_resources_list() -> Dict[str, Any]:
    """Return the static resource list."""
    return {"resources": _list_resources()}


def _handle_resources_read(params: Dict[str, Any],
                            install_dir: Optional[Path]) -> Dict[str, Any]:
    """Dispatch a resources/read by URI."""
    uri = params.get("uri")
    if not isinstance(uri, str):
        return {"contents": [{"uri": "", "mimeType": "text/plain",
                              "text": "resources/read: missing uri"}],
                "isError": True}
    if uri.startswith("runaway://brief/"):
        return {"contents": [_resource_brief(uri, install_dir)]}
    if uri == "runaway://stats":
        return {"contents": [_resource_stats(install_dir)]}
    if uri == "runaway://hard-rules":
        return {"contents": [_resource_hard_rules(install_dir)]}
    return {"contents": [{"uri": uri, "mimeType": "text/plain",
                          "text": f"unknown resource {uri!r}"}],
            "isError": True}


def dispatch(message: Dict[str, Any],
             install_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Route a single JSON-RPC message to its handler and return the response.

    Returns:
        A JSON-RPC response dict, or ``None`` for notifications (messages
        without an ``id``).

    Refuses:
        Malformed messages (returns an error response with code
        ``-32600`` / ``-32601`` / ``-32602``).
    """
    if not isinstance(message, dict):
        return _build_response(
            None,
            error={"code": ERR_INVALID_REQUEST,
                   "message": "request must be a JSON object"},
        )
    if message.get("jsonrpc") != JSONRPC_VERSION:
        return _build_response(
            message.get("id"),
            error={"code": ERR_INVALID_REQUEST,
                   "message": f"jsonrpc must be {JSONRPC_VERSION!r}"},
        )
    method = message.get("method")
    if not isinstance(method, str):
        return _build_response(
            message.get("id"),
            error={"code": ERR_INVALID_REQUEST,
                   "message": "method must be a string"},
        )
    req_id = message.get("id")
    params = message.get("params") or {}

    is_notification = ("id" not in message) or (req_id is None)

    try:
        if method == "initialize":
            result = _handle_initialize(params)
        elif method in ("initialized", "notifications/initialized"):
            return None  # notification, no response
        elif method == "tools/list":
            result = _handle_tools_list()
        elif method == "tools/call":
            result = _handle_tools_call(params, install_dir)
        elif method == "resources/list":
            result = _handle_resources_list()
        elif method == "resources/read":
            result = _handle_resources_read(params, install_dir)
        elif method in ("shutdown", "exit"):
            result = {"ok": True}
        elif method == "ping":
            result = {}
        else:
            return _build_response(
                req_id,
                error={"code": ERR_METHOD_NOT_FOUND,
                       "message": f"method not found: {method!r}"},
            )
    except Exception as exc:  # surface, never crash the loop (HR-10)
        _log.exception("dispatch failed for %s", method)
        return _build_response(
            req_id,
            error={"code": ERR_INTERNAL,
                   "message": f"{type(exc).__name__}: {exc}",
                   "data": {"traceback": traceback.format_exc()}},
        )

    if is_notification:
        return None
    return _build_response(req_id, result=result)


# ---------------------------------------------------------------- transports

def _read_content_length_message(reader: Any) -> Optional[Dict[str, Any]]:
    """Read one Content-Length-framed JSON-RPC message from ``reader``.

    ``reader`` must be a binary stream supporting ``readline()`` and
    ``read(n)``. Headers are CRLF-delimited ASCII; the body is exactly
    ``Content-Length`` bytes of UTF-8 JSON. A blank-line header section
    (just ``\\r\\n``) before any ``Content-Length`` header is treated as
    EOF.

    Returns:
        The parsed JSON message dict, or ``None`` on EOF.

    Refuses:
        Missing ``Content-Length`` header (raises :class:`ValueError`),
        non-integer ``Content-Length`` (raises :class:`ValueError`),
        truncated body (raises :class:`EOFError`), invalid JSON
        (raises :class:`json.JSONDecodeError`).
    """
    content_length: Optional[int] = None
    saw_any_header = False
    while True:
        line = reader.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            if not saw_any_header:
                # blank framing-only chunk between messages — keep reading
                continue
            break
        saw_any_header = True
        try:
            text = line.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"non-ASCII header line: {exc}") from exc
        if ":" not in text:
            raise ValueError(f"malformed header line: {text!r}")
        name, _, value = text.partition(":")
        if name.strip().lower() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError as exc:
                raise ValueError(
                    f"Content-Length not an integer: {value!r}"
                ) from exc

    if content_length is None:
        raise ValueError("missing Content-Length header")
    if content_length < 0:
        raise ValueError(f"negative Content-Length: {content_length}")
    body = reader.read(content_length)
    if len(body) != content_length:
        raise EOFError(
            f"truncated body: expected {content_length} bytes, got {len(body)}"
        )
    return json.loads(body.decode("utf-8"))


def _write_content_length_message(writer: Any, payload: Dict[str, Any]) -> None:
    """Serialize ``payload`` and emit it with Content-Length framing.

    ``writer`` must be a binary stream (e.g. ``sys.stdout.buffer``). The
    JSON body is UTF-8 encoded; the header is ASCII.

    Returns:
        None.

    Refuses:
        Payloads that ``json.dumps`` cannot encode (raises
        :class:`TypeError` after ``default=str`` fallback).
    """
    body = json.dumps(payload, default=str).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    writer.write(header)
    writer.write(body)
    writer.flush()


def serve_stdio(install_dir: Optional[Path] = None,
                stdin: Any = None, stdout: Any = None,
                framing: Optional[str] = None) -> None:
    """Run the server over stdio in the requested framing mode.

    Stops on EOF or after a ``shutdown`` request followed by ``exit`` (or
    EOF). Notifications produce no response. Every response is flushed
    immediately. The framing mode is resolved by :func:`_resolve_framing`
    (default :data:`FRAMING_CONTENT_LENGTH`, env override
    ``RC_MCP_FRAMING=ndjson``).

    Returns:
        None.

    Refuses:
        Messages that are not valid JSON / framed correctly (emits a
        parse-error JSON-RPC response and keeps reading — HR-10: never
        silent).
    """
    mode = _resolve_framing(framing)
    if mode == FRAMING_NDJSON:
        _serve_stdio_ndjson(install_dir=install_dir, stdin=stdin, stdout=stdout)
        return
    _serve_stdio_content_length(install_dir=install_dir,
                                stdin=stdin, stdout=stdout)


def _serve_stdio_content_length(install_dir: Optional[Path],
                                 stdin: Any, stdout: Any) -> None:
    """Content-Length stdio loop. See :func:`serve_stdio` for semantics.

    Returns:
        None.

    Refuses:
        Malformed headers or bodies (emits a JSON-RPC parse error and
        continues — HR-10).
    """
    inp = stdin if stdin is not None else getattr(sys.stdin, "buffer", sys.stdin)
    out = stdout if stdout is not None else getattr(sys.stdout, "buffer", sys.stdout)
    shutting_down = False

    while True:
        try:
            message = _read_content_length_message(inp)
        except (ValueError, EOFError, json.JSONDecodeError) as exc:
            err = _build_response(
                None,
                error={"code": ERR_PARSE,
                       "message": f"parse error: {exc}"},
            )
            try:
                _write_content_length_message(out, err)
            except OSError as write_exc:
                _log.error("failed to write parse-error response: %s", write_exc)
                break
            continue

        if message is None:
            break

        if not isinstance(message, dict):
            err = _build_response(
                None,
                error={"code": ERR_INVALID_REQUEST,
                       "message": "request must be a JSON object"},
            )
            _write_content_length_message(out, err)
            continue

        response = dispatch(message, install_dir=install_dir)
        if response is not None:
            _write_content_length_message(out, response)

        if message.get("method") == "shutdown":
            shutting_down = True
        elif message.get("method") == "exit" and shutting_down:
            break


def _serve_stdio_ndjson(install_dir: Optional[Path],
                         stdin: Any, stdout: Any) -> None:
    """Newline-delimited JSON stdio loop. See :func:`serve_stdio`.

    Returns:
        None.

    Refuses:
        Lines that are not valid JSON (emits a parse-error response and
        keeps reading — HR-10).
    """
    inp = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    shutting_down = False

    for raw_line in inp:
        line = raw_line.strip() if isinstance(raw_line, str) \
            else raw_line.decode("utf-8").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            err = _build_response(
                None,
                error={"code": ERR_PARSE,
                       "message": f"parse error: {exc}"},
            )
            out.write(json.dumps(err) + "\n")
            out.flush()
            continue

        response = dispatch(message, install_dir=install_dir)
        if response is not None:
            out.write(json.dumps(response, default=str) + "\n")
            out.flush()

        if isinstance(message, dict) and message.get("method") == "shutdown":
            shutting_down = True
        elif isinstance(message, dict) and message.get("method") == "exit" \
                and shutting_down:
            break


def serve_test(messages: List[Dict[str, Any]],
               install_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Process a list of inbound messages and return the responses (no stdio).

    Useful in unit tests — no file descriptors, no event loop. Notifications
    (no ``id``) are dispatched but their ``None`` response is filtered out
    of the return value.

    Returns:
        The list of response dicts in the order they were produced.

    Refuses:
        ``messages`` that is not a list (raises :class:`TypeError`).
    """
    if not isinstance(messages, list):
        raise TypeError("messages must be a list of dicts")
    out: List[Dict[str, Any]] = []
    for m in messages:
        r = dispatch(m, install_dir=install_dir)
        if r is not None:
            out.append(r)
    return out


__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "FRAMING_CONTENT_LENGTH",
    "FRAMING_NDJSON",
    "dispatch",
    "serve_stdio",
    "serve_test",
]
