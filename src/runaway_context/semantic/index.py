"""Semantic index — embed records, score similarity, blend with FTS (E12, E13).

This module owns the read/write paths for ``embedding_meta`` and
``embedding_payload``. When the optional ``sqlite-vec`` extension is loaded
the ``vec0`` virtual table would speed up search; for the reference
implementation we do cosine scoring in pure Python so the system works on
stock SQLite.

HR-1: no network. The provider is injected by the caller (see
:mod:`runaway_context.semantic.encoder`). This module never imports
``urllib``, ``socket``, ``http``, ``requests``, ``httpx`` or ``aiohttp``.

HR-3: this module never deletes embeddings. ``embedding_meta`` rows are
soft-cascade via the underlying record's ``deleted_at`` (filtered at read).

Refuses:
    Embedding rows that are soft-deleted (``deleted_at IS NOT NULL``).
    Returning matches for tables outside the allowlist.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Dict, List, Optional

from runaway_context._db import transaction
from runaway_context.retrieval import (
    hybrid_score,
    search_chunks_fts,
    search_lessons_fts,
)
from runaway_context.semantic.encoder import (
    LocalDeterministicProvider,
    Provider,
    pack_vector,
    unpack_vector,
)


_ALLOWED_TABLES = ("knowledge_chunks", "lessons_learned")


def cosine(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two equally-sized float vectors.

    Returns:
        A float in ``[-1.0, 1.0]``. Returns ``0.0`` for either vector with
        zero norm (avoids divide-by-zero — and a zero vector has no
        direction so similarity is undefined; ``0.0`` is the conservative
        non-match).

    Raises:
        ValueError: when the vectors have different lengths.

    Refuses:
        Silently truncating mismatched vectors.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _record_text(conn: sqlite3.Connection, table: str, record_id: int) -> str:
    """Fetch the embeddable text for a single record (title + body).

    Returns:
        A string of the form ``"<title>\\n\\n<body>"`` with empty
        components elided.

    Raises:
        ValueError: when *table* is not in the allowlist.
        LookupError: when no row exists for *record_id*.

    Refuses:
        Returning soft-deleted rows.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"table not allowed: {table!r}")
    if table == "knowledge_chunks":
        row = conn.execute(
            "SELECT title, body FROM knowledge_chunks "
            "WHERE id = ? AND deleted_at IS NULL",
            (int(record_id),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT title, what_happened, why, prevention_rule "
            "FROM lessons_learned "
            "WHERE id = ? AND deleted_at IS NULL",
            (int(record_id),),
        ).fetchone()
    if row is None:
        raise LookupError(
            f"no active row in {table} with id={record_id}"
        )
    parts: List[str] = []
    for key in row.keys():
        value = row[key]
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(parts)


def _l2_norm(vec: List[float]) -> float:
    """Return the L2 norm of *vec* (used as a diagnostic in embedding_meta)."""
    return math.sqrt(sum(x * x for x in vec))


def embed_record(
    conn: sqlite3.Connection,
    table: str,
    record_id: int,
    provider: Provider,
) -> int:
    """Compute and store an embedding for a single record.

    Side effects:
        Upserts one row into ``embedding_meta`` and ``embedding_payload``
        keyed on ``(table, record_id, provider.provider_name)``. Re-running
        with a changed provider name produces a new row; re-running with
        the same provider name overwrites the prior vector.

    Returns:
        The ``embedding_meta.id`` of the resulting row.

    Raises:
        ValueError: when *table* is not in the allowlist or the provider
            returns an empty vector.
        LookupError: when *record_id* does not match an active row.

    Refuses:
        Embedding soft-deleted rows. Storing a vector whose length disagrees
        with ``provider.dim``.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"table not allowed: {table!r}")
    text = _record_text(conn, table, record_id)
    vectors = provider.embed([text])
    if not vectors or not vectors[0]:
        raise ValueError("provider returned an empty embedding")
    vec = vectors[0]
    if len(vec) != int(provider.dim):
        raise ValueError(
            f"provider {provider.provider_name!r} returned dim={len(vec)} "
            f"but advertises dim={provider.dim}"
        )
    blob = pack_vector(vec)
    norm = _l2_norm(vec)

    with transaction(conn):
        existing = conn.execute(
            "SELECT id FROM embedding_meta "
            "WHERE table_name = ? AND record_id = ? AND provider = ?",
            (table, int(record_id), provider.provider_name),
        ).fetchone()
        if existing is not None:
            meta_id = int(existing["id"])
            conn.execute(
                "UPDATE embedding_meta SET dim = ?, norm = ?, "
                "created_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(provider.dim), float(norm), meta_id),
            )
            conn.execute(
                "UPDATE embedding_payload SET vec = ? WHERE embedding_meta_id = ?",
                (blob, meta_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO embedding_meta "
                "(table_name, record_id, provider, dim, norm) "
                "VALUES (?, ?, ?, ?, ?)",
                (table, int(record_id), provider.provider_name,
                 int(provider.dim), float(norm)),
            )
            meta_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO embedding_payload (embedding_meta_id, vec) "
                "VALUES (?, ?)",
                (meta_id, blob),
            )
    return meta_id


def _record_join(table: str) -> str:
    """Return SQL that joins active rows of *table* onto embedding_meta."""
    return (
        f"JOIN {table} r ON r.id = m.record_id "
        f"WHERE r.deleted_at IS NULL "
    )


def search_similar(
    conn: sqlite3.Connection,
    query: str,
    provider: Provider,
    *,
    table: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Find records whose stored vectors are nearest to *query* by cosine.

    The reference implementation scans every stored vector for the
    requested provider and computes cosine in Python. For installs with
    ``sqlite-vec`` loaded this is the slow path — adopters can override
    by registering a vec0 sidecar.

    Returns:
        A list of dicts with the underlying record's columns plus
        ``vector_score`` (cosine similarity, higher = better).

    Raises:
        ValueError: when *table* is not in the allowlist, *query* is
            empty, or *limit* is non-positive.

    Refuses:
        Returning soft-deleted records. Mixing providers — only rows
        embedded with ``provider.provider_name`` are scored.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"table not allowed: {table!r}")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if int(limit) <= 0:
        raise ValueError("limit must be positive")

    qvec = provider.embed([query])[0]
    if len(qvec) != int(provider.dim):
        raise ValueError(
            "query vector dim does not match provider.dim"
        )

    rows = conn.execute(
        "SELECT m.id AS meta_id, m.record_id, m.dim, p.vec "
        "FROM embedding_meta m "
        "JOIN embedding_payload p ON p.embedding_meta_id = m.id "
        f"{_record_join(table)} "
        "  AND m.table_name = ? AND m.provider = ? ",
        (table, provider.provider_name),
    ).fetchall()

    scored: List[Dict[str, Any]] = []
    for row in rows:
        vec = unpack_vector(row["vec"], int(row["dim"]))
        score = cosine(qvec, vec)
        scored.append({
            "record_id": int(row["record_id"]),
            "meta_id": int(row["meta_id"]),
            "vector_score": float(score),
        })
    scored.sort(key=lambda d: d["vector_score"], reverse=True)
    top = scored[: int(limit)]
    if not top:
        return []

    ids = [d["record_id"] for d in top]
    placeholders = ",".join("?" * len(ids))
    rec_rows = conn.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders}) AND deleted_at IS NULL",
        ids,
    ).fetchall()
    rec_by_id = {int(r["id"]): {k: r[k] for k in r.keys()} for r in rec_rows}

    out: List[Dict[str, Any]] = []
    for entry in top:
        rec = rec_by_id.get(entry["record_id"])
        if rec is None:
            continue
        rec["vector_score"] = entry["vector_score"]
        rec["embedding_meta_id"] = entry["meta_id"]
        out.append(rec)
    return out


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    table: str,
    provider: Optional[Provider] = None,
    alpha: float = 0.6,
    limit: int = 20,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Combine FTS5 and vector search with the weight in :func:`hybrid_score`.

    Algorithm:
        1. Run FTS5 search (``search_chunks_fts`` or ``search_lessons_fts``).
        2. If *provider* is supplied, run :func:`search_similar`.
        3. Index both result sets by record id, compute a blended score,
           sort ascending (lower = better, matching the FTS sign).

    When *provider* is None the function returns the FTS results
    unchanged. This is the default for environments without embeddings.

    Returns:
        A list of dicts: each is the underlying record row plus
        ``fts_score`` (when available), ``vector_score`` (when available),
        and ``hybrid_score``. Sorted by ``hybrid_score`` ascending.

    Raises:
        ValueError: when *table* is not in the allowlist, *query* is empty,
            *alpha* is outside [0, 1], or *limit* is non-positive.

    Refuses:
        Returning soft-deleted rows. Mixing providers in the same call.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"table not allowed: {table!r}")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if alpha < 0.0 or alpha > 1.0:
        raise ValueError("alpha must be in [0, 1]")
    if int(limit) <= 0:
        raise ValueError("limit must be positive")

    if table == "knowledge_chunks":
        fts_rows = search_chunks_fts(conn, query, project=project, limit=limit * 2)
    else:
        fts_rows = search_lessons_fts(conn, query, project=project, limit=limit * 2)

    vec_rows: List[Dict[str, Any]] = []
    if provider is not None:
        vec_rows = search_similar(conn, query, provider, table=table, limit=limit * 2)

    by_id: Dict[int, Dict[str, Any]] = {}
    for row in fts_rows:
        rid = int(row["id"])
        by_id.setdefault(rid, dict(row))
        by_id[rid]["fts_score"] = float(row.get("fts_score", 0.0))
    for row in vec_rows:
        rid = int(row["id"])
        merged = by_id.setdefault(rid, dict(row))
        merged["vector_score"] = float(row.get("vector_score", 0.0))
        # Carry the embedding_meta_id forward if FTS row didn't have it.
        if "embedding_meta_id" not in merged and "embedding_meta_id" in row:
            merged["embedding_meta_id"] = row["embedding_meta_id"]

    scored: List[Dict[str, Any]] = []
    for rid, row in by_id.items():
        fts = row.get("fts_score")
        vec = row.get("vector_score")
        # hybrid_score expects a numeric fts; if FTS didn't match this row,
        # assign a neutral high (i.e. low-relevance) value so vec can still rank it.
        fts_value = float(fts) if fts is not None else 0.0
        row["hybrid_score"] = hybrid_score(
            fts_score=fts_value,
            vector_score=vec,
            alpha=alpha,
        )
        scored.append(row)
    scored.sort(key=lambda d: d["hybrid_score"])
    return scored[: int(limit)]


def default_provider() -> Provider:
    """Return the deterministic local fallback provider.

    Returns:
        A :class:`LocalDeterministicProvider` instance. Convenience accessor
        used by the reindex script and contract tests when no config is
        loaded.

    Refuses:
        Nothing — pure factory.
    """
    return LocalDeterministicProvider()


__all__ = [
    "cosine",
    "embed_record",
    "search_similar",
    "hybrid_search",
    "default_provider",
]
