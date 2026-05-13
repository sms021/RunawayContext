"""E12 — semantic encoder + index — extra branches."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.semantic.encoder import (
    LocalDeterministicProvider,
    load_provider,
    pack_vector,
    unpack_vector,
)
from runaway_context.semantic.index import (
    cosine,
    embed_record,
    hybrid_search,
    search_similar,
    default_provider,
    _record_text,
)

pytestmark = pytest.mark.feature


def test_local_provider_dim_validation():
    with pytest.raises(ValueError):
        LocalDeterministicProvider(dim=0)
    with pytest.raises(ValueError):
        LocalDeterministicProvider(dim=-5)


def test_local_provider_embed_refuses_single_string():
    prov = LocalDeterministicProvider()
    with pytest.raises(TypeError):
        prov.embed("not a list")


def test_local_provider_embed_refuses_non_string_in_list():
    prov = LocalDeterministicProvider()
    with pytest.raises(TypeError):
        prov.embed(["ok", 5])


def test_load_provider_invalid_name():
    with pytest.raises(ValueError):
        load_provider("")
    with pytest.raises(ValueError):
        load_provider("not-a-known-provider")


def test_load_provider_mpnet_variant():
    prov = load_provider("sentence-transformers-mpnet-base-v2")
    assert prov.dim == 768


def test_load_provider_local_deterministic_name():
    prov = load_provider("local-deterministic")
    assert prov.dim > 0


def test_unpack_vector_bad_length():
    with pytest.raises(ValueError):
        unpack_vector(b"\x00\x00", 5)


def test_cosine_mismatch_length():
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_cosine_zero_norm_returns_zero():
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [0.0, 0.0]) == 0.0


def test_embed_record_invalid_table(seeded_client):
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            embed_record(conn, "bad_table", 1, prov)
    finally:
        conn.close()


def test_record_text_invalid_table(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            _record_text(conn, "bad_table", 1)
    finally:
        conn.close()


def test_record_text_missing_row(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(LookupError):
            _record_text(conn, "lessons_learned", 999999)
    finally:
        conn.close()


def test_search_similar_validation(seeded_client):
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            search_similar(conn, "q", prov, table="bogus", limit=5)
        with pytest.raises(ValueError):
            search_similar(conn, "", prov, table="knowledge_chunks", limit=5)
        with pytest.raises(ValueError):
            search_similar(conn, "q", prov, table="knowledge_chunks", limit=0)
    finally:
        conn.close()


def test_hybrid_search_validation(seeded_client):
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError):
            hybrid_search(conn, "q", table="bogus")
        with pytest.raises(ValueError):
            hybrid_search(conn, "", table="knowledge_chunks")
        with pytest.raises(ValueError):
            hybrid_search(conn, "q", table="knowledge_chunks", alpha=2.0)
        with pytest.raises(ValueError):
            hybrid_search(conn, "q", table="knowledge_chunks", limit=0)
    finally:
        conn.close()


def test_hybrid_search_without_provider(seeded_client):
    """hybrid_search with provider=None falls back to FTS-only."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        # Need to query an FTS5-indexed text term
        res = hybrid_search(conn, "CLI", table="knowledge_chunks",
                            provider=None, limit=5)
        assert isinstance(res, list)
    finally:
        conn.close()


def test_hybrid_search_with_provider(seeded_client):
    """hybrid_search with provider combines FTS + vector scoring."""
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        chunks = conn.execute(
            "SELECT id FROM knowledge_chunks WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        for c in chunks:
            embed_record(conn, "knowledge_chunks", int(c["id"]), prov)
        res = hybrid_search(conn, "CLI entrypoint", table="knowledge_chunks",
                            provider=prov, alpha=0.5, limit=5)
        assert isinstance(res, list)
    finally:
        conn.close()


def test_default_provider():
    out = default_provider()
    assert isinstance(out, LocalDeterministicProvider)


def test_embed_record_overwrite(seeded_client):
    """Re-embedding the same record updates instead of inserting."""
    prov = LocalDeterministicProvider()
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        cid = conn.execute(
            "SELECT id FROM knowledge_chunks LIMIT 1"
        ).fetchone()["id"]
        mid1 = embed_record(conn, "knowledge_chunks", cid, prov)
        mid2 = embed_record(conn, "knowledge_chunks", cid, prov)
        assert mid1 == mid2
    finally:
        conn.close()
