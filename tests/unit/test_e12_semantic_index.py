"""E12 — semantic index: embed → search."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.semantic import (
    LocalDeterministicProvider, cosine, embed_record, load_provider,
    pack_vector, search_similar, unpack_vector,
)

pytestmark = pytest.mark.feature


def test_e12_local_provider_deterministic():
    """E12: LocalDeterministicProvider returns identical vectors for same text."""
    prov = LocalDeterministicProvider()
    vecs = prov.embed(["alpha"])
    again = prov.embed(["alpha"])
    assert vecs == again


def test_e12_pack_unpack_round_trip():
    """E12: pack_vector / unpack_vector survive a round-trip."""
    vec = [0.1, 0.5, -0.2]
    blob = pack_vector(vec)
    out = unpack_vector(blob, len(vec))
    for a, b in zip(vec, out):
        assert abs(a - b) < 1e-6


def test_e12_cosine_basic():
    """E12: cosine of identical vectors is 1.0."""
    v = [1.0, 0.0, 0.0]
    assert abs(cosine(v, v) - 1.0) < 1e-6


def test_e12_embed_and_search_round_trip(seeded_client):
    """E12: embed_record then search_similar returns the embedded row."""
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        chunks = conn.execute(
            "SELECT id FROM knowledge_chunks WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        for c in chunks:
            embed_record(conn, "knowledge_chunks", int(c["id"]), prov)
        results = search_similar(
            conn, query="CLI entrypoint", provider=prov,
            table="knowledge_chunks", limit=5,
        )
        assert results
        assert "vector_score" in results[0]
    finally:
        conn.close()


def test_e12_load_provider_default():
    """E12: load_provider returns the local fallback for sentence-transformers names."""
    prov = load_provider("sentence-transformers-MiniLM-L6-v2")
    assert prov.provider_name == "sentence-transformers-MiniLM-L6-v2"
    assert prov.dim > 0
