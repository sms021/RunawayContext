"""E19 — curation — extra branches."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runaway_context.curation import (
    CurationEngine,
    _jaccard,
    _project_tags,
    _tokenize,
)

pytestmark = pytest.mark.feature


def test_tokenize_strips_stopwords_and_short():
    out = _tokenize("the quick fox runs")
    assert "quick" in out
    assert "fox" in out
    assert "runs" in out
    assert "the" not in out  # stopword


def test_tokenize_handles_empty():
    assert _tokenize("") == set()
    assert _tokenize(None) == set()


def test_jaccard_edge_cases():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard({"a"}, {"a"}) == 1.0


def test_project_tags_parses_variants():
    assert _project_tags(None) == set()
    assert _project_tags("") == set()
    assert _project_tags("not json") == set()
    assert _project_tags('{"obj": 1}') == set()  # not a list
    assert _project_tags('["a","b"]') == {"a", "b"}


def test_find_duplicates_invalid_threshold(seeded_client):
    engine = CurationEngine(seeded_client._knowledge_db)
    with pytest.raises(ValueError):
        engine.find_duplicates(similarity_threshold=1.5)
    with pytest.raises(ValueError):
        engine.find_duplicates(similarity_threshold=-0.1)


def test_find_duplicates_fires(seeded_client):
    """Insert two near-identical lessons and verify dedup detection."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        # Append two new lessons with very similar prevention rules
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, project_tags, severity, status, "
            " prevention_rule, slug, maturity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tooling", "Dup A", '["tooling"]', "info", "active",
             "Always verify the changeset before bulk delete",
             "dup-a", "active"),
        )
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, project_tags, severity, status, "
            " prevention_rule, slug, maturity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tooling", "Dup B", '["tooling"]', "info", "active",
             "Always verify the changeset before bulk delete",
             "dup-b", "active"),
        )
        conn.commit()
    finally:
        conn.close()
    engine = CurationEngine(db)
    out = engine.find_duplicates(similarity_threshold=0.5)
    assert out


def test_find_dead_lessons_validates_days(seeded_client):
    engine = CurationEngine(seeded_client._knowledge_db)
    with pytest.raises(ValueError):
        engine.find_dead_lessons(days=-1)


def test_find_dead_lessons_excludes_archived(seeded_client):
    """Archived/superseded lessons are excluded from dead-list."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        lid = conn.execute(
            "SELECT id FROM lessons_learned LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, updated_at = ?, "
            "maturity = 'archived' WHERE id = ?",
            (old, old, lid),
        )
        conn.commit()
    finally:
        conn.close()
    engine = CurationEngine(db)
    out = engine.find_dead_lessons(days=180)
    assert not any(r["id"] == lid for r in out)


def test_find_dead_lessons_fires(seeded_client):
    """Old active lesson with no touch → flagged dead."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, updated_at = ?, "
            "last_revisited = NULL, maturity = 'active' WHERE id = ?",
            (old, old, lid),
        )
        conn.commit()
    finally:
        conn.close()
    engine = CurationEngine(db)
    out = engine.find_dead_lessons(days=180)
    assert any(r["id"] == lid for r in out)


def test_find_supersession_candidates(seeded_client):
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old_t = "2020-01-01 00:00:00"
        new_t = "2026-01-01 00:00:00"
        rule = ("Always verify changesets before bulk delete and snapshot first")
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, slug, project_tags, prevention_rule, "
            " severity, status, maturity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("tooling", "older", "shared_slug", '["tooling"]', rule,
             "info", "active", "active", old_t),
        )
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, slug, project_tags, prevention_rule, "
            " severity, status, maturity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("tooling", "newer", "shared_slug", '["tooling"]', rule,
             "info", "active", "active", new_t),
        )
        conn.commit()
    finally:
        conn.close()
    engine = CurationEngine(db)
    out = engine.find_supersession_candidates()
    assert out


def test_propose_all_writes_suggestions(seeded_client):
    """propose_all persists suggested_maturity for dead lessons."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        lid = conn.execute("SELECT id FROM lessons_learned LIMIT 1").fetchone()[0]
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, updated_at = ?, "
            "last_revisited = NULL, maturity = 'active' WHERE id = ?",
            (old, old, lid),
        )
        conn.commit()
    finally:
        conn.close()
    engine = CurationEngine(db)
    out = engine.propose_all()
    assert out["applied_suggestions"] >= 1
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT suggested_maturity FROM lessons_learned WHERE id = ?",
            (lid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "archived"
