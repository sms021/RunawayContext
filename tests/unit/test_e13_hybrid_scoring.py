"""E13 — hybrid scoring + retrieval."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.retrieval import (
    _sanitize_query,
    hybrid_score,
    search_chunks_fts,
    search_lessons_fts,
)
from runaway_context.semantic import LocalDeterministicProvider, embed_record, hybrid_search

pytestmark = pytest.mark.feature


def test_e13_hybrid_score_pure():
    """E13: hybrid_score blends FTS and vector scores by alpha."""
    out = hybrid_score(fts_score=1.0, vector_score=0.5, alpha=0.6)
    assert isinstance(out, float)


def test_e13_search_chunks_fts(seeded_client):
    """E13: search_chunks_fts returns matches from knowledge_chunks_fts."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = search_chunks_fts(conn, "CLI", limit=10)
    finally:
        conn.close()
    assert isinstance(rows, list)


@pytest.mark.parametrize(
    "raw",
    [
        "client-authoritative multiplayer",
        "foo:bar",
        "alpha + beta",
        "wild*",
        "(nested) groups",
        '"unbalanced',
        "trailing-",
        "-leading",
        "AND OR NOT",
    ],
)
def test_e13_sanitize_query_neutralises_fts5_operators(raw):
    """Regression: every FTS5 operator char must be inert after sanitisation."""
    safe = _sanitize_query(raw)
    assert '"' in safe, f"sanitised query must phrase-quote tokens: {safe!r}"
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            "CREATE VIRTUAL TABLE t USING fts5(body);"
            "INSERT INTO t(body) VALUES ('hello world');"
        )
        conn.execute("SELECT rowid FROM t WHERE t MATCH ?", (safe,)).fetchall()
    finally:
        conn.close()


def test_e13_search_lessons_fts_survives_operator_chars(seeded_client):
    """End-to-end: operator chars in user input must not raise."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = search_lessons_fts(conn, "client-authoritative multiplayer", limit=3)
    finally:
        conn.close()
    assert isinstance(rows, list)


def test_e13_hybrid_search_blends(seeded_client):
    """E13: hybrid_search returns a blended list when a provider is supplied."""
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        for r in conn.execute(
            "SELECT id FROM knowledge_chunks WHERE deleted_at IS NULL"
        ).fetchall():
            embed_record(conn, "knowledge_chunks", int(r["id"]), prov)
        out = hybrid_search(
            conn, "CLI entrypoint",
            table="knowledge_chunks", provider=prov, alpha=0.6, limit=5,
        )
    finally:
        conn.close()
    assert isinstance(out, list)
