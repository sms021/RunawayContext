"""E19 — automated curation (dedup + dead-lesson + supersession).

HR-9 spot check: the curation engine writes only to ``suggested_maturity``.
"""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.curation import CurationEngine

pytestmark = pytest.mark.feature


def test_e19_find_duplicates(seeded_client):
    """E19: find_duplicates returns a list (may be empty for the seed set)."""
    engine = CurationEngine(seeded_client._knowledge_db)
    out = engine.find_duplicates(similarity_threshold=0.5)
    assert isinstance(out, list)


def test_e19_find_dead_lessons(seeded_client):
    """E19: find_dead_lessons returns a list of stale lesson dicts."""
    engine = CurationEngine(seeded_client._knowledge_db)
    out = engine.find_dead_lessons(days=1)
    assert isinstance(out, list)


def test_e19_find_supersession_candidates(seeded_client):
    """E19: find_supersession_candidates returns a list."""
    engine = CurationEngine(seeded_client._knowledge_db)
    out = engine.find_supersession_candidates()
    assert isinstance(out, list)


def test_e19_propose_all_writes_only_suggested_maturity(seeded_client):
    """E19 + HR-9: propose_all never touches the maturity column."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        before = conn.execute(
            "SELECT id, maturity FROM lessons_learned"
        ).fetchall()
    finally:
        conn.close()
    engine = CurationEngine(db)
    engine.propose_all()
    conn = sqlite3.connect(str(db))
    try:
        after = conn.execute(
            "SELECT id, maturity FROM lessons_learned"
        ).fetchall()
    finally:
        conn.close()
    # Maturity for each row must be unchanged.
    by_id_before = {r[0]: r[1] for r in before}
    by_id_after = {r[0]: r[1] for r in after}
    assert by_id_before == by_id_after
