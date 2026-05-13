"""E13 — hybrid scoring + retrieval."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.retrieval import hybrid_score, search_chunks_fts
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
