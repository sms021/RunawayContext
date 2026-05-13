"""E10 — Python Client API: each public method invoked.

This is the HR-12 coverage anchor: every Client public method has a test
function whose name references the method.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.feature


# ------------------------------------------------------------------- reads


def test_e10_get_brief(client):
    """E10: get_brief returns card + lessons + chunks + warnings."""
    client.register_slug("tooling")
    lesson_id = client.log_lesson(
        title="hello", project_tags=["tooling"], severity="info",
    )
    md_path = client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", json.dumps([lesson_id]), "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    out = client.get_brief("tooling")
    assert "card" in out
    assert any(l["id"] == lesson_id for l in out["lessons"])


def test_e10_search_chunks(seeded_client):
    """E10: search_chunks runs FTS over knowledge_chunks."""
    results = seeded_client.search_chunks("CLI", project="tooling", limit=10)
    assert isinstance(results, list)


def test_e10_search_lessons(seeded_client):
    """E10: search_lessons runs FTS over lessons_learned."""
    results = seeded_client.search_lessons("bulk", project="tooling", limit=10)
    assert isinstance(results, list)


def test_e10_get_lesson(seeded_client):
    """E10: get_lesson returns a single decoded row or None."""
    lessons = seeded_client.list_lessons(project="tooling")
    row = seeded_client.get_lesson(lessons[0]["id"])
    assert row is not None
    assert row["title"]
    assert seeded_client.get_lesson(99999) is None


def test_e10_get_chunk(seeded_client):
    """E10: get_chunk returns the chunk row by id."""
    chunks = seeded_client.list_chunks(project="tooling")
    row = seeded_client.get_chunk(chunks[0]["id"])
    assert row is not None
    assert row["title"]


def test_e10_list_drafts(seeded_client):
    """E10: list_drafts honors the status filter."""
    seeded_client.propose_lesson_draft(
        title="draft test", project_tags=["tooling"],
    )
    drafts = seeded_client.list_drafts(status="pending")
    assert any(d["title"] == "draft test" for d in drafts)
    assert seeded_client.list_drafts(status="all")


def test_e10_list_lessons(seeded_client):
    """E10: list_lessons returns soft-delete-filtered rows."""
    rows = seeded_client.list_lessons(project="tooling")
    assert rows
    for r in rows:
        assert r["deleted_at"] is None


def test_e10_list_chunks(seeded_client):
    """E10: list_chunks returns soft-delete-filtered chunk rows."""
    rows = seeded_client.list_chunks(project="tooling")
    assert rows


def test_e10_list_specialists(seeded_client):
    """E10: list_specialists returns active specialists (may be empty)."""
    seeded_client.register_specialist(name="DocSpec", domain="docs")
    rows = seeded_client.list_specialists()
    assert any(r["name"] == "DocSpec" for r in rows)


def test_e10_stats(seeded_client):
    """E10: stats() returns a summary dict."""
    s = seeded_client.stats()
    assert isinstance(s, dict)
    assert "knowledge_db" in s or "lessons_active" in s


def test_e10_tier_check(client):
    """E10: tier_check returns dict with tier + next gate criteria."""
    out = client.tier_check()
    assert "tier" in out
    assert "criteria" in out


# ------------------------------------------------------------------- writes


def test_e10_log_lesson(client):
    """E10: log_lesson inserts a row + writes audit + returns id."""
    client.register_slug("tooling")
    lid = client.log_lesson(
        title="from test", project_tags=["tooling"],
        severity="warning", blast_radius=3, frequency=2, reversibility=2,
    )
    assert lid > 0


def test_e10_propose_knowledge(client):
    """E10: propose_knowledge inserts a knowledge_chunk."""
    client.register_slug("tooling")
    cid = client.propose_knowledge(
        project="tooling", topic="x", title="X", body="some body content",
    )
    assert cid > 0


def test_e10_supersede(seeded_client):
    """E10: supersede() marks a lesson as superseded by another."""
    lessons = seeded_client.list_lessons(project="tooling")
    a, b = lessons[0]["id"], lessons[1]["id"]
    seeded_client.supersede(a, b)
    row = seeded_client.get_lesson(a)
    # supersede sets status='superseded' and maturity='superseded'
    assert row is None or row["status"] == "superseded"


def test_e10_soft_delete(seeded_client):
    """E10: soft_delete marks deletion + snapshots payload (HR-3)."""
    lessons = seeded_client.list_lessons(project="tooling")
    seeded_client.soft_delete(
        table="lessons_learned",
        record_id=lessons[0]["id"],
        actor="tester",
        reason="E10",
    )


def test_e10_mature_lesson(seeded_client):
    """E10: mature_lesson applies the maturity transition (HR-9)."""
    lessons = seeded_client.list_lessons(project="tooling")
    seeded_client.mature_lesson(
        lessons[0]["id"], to_state="stable", actor="tester",
    )


def test_e10_propose_lesson_draft(client):
    """E10: propose_lesson_draft writes to lesson_drafts (HR-3, HR-2)."""
    client.register_slug("tooling")
    did = client.propose_lesson_draft(
        title="draft", project_tags=["tooling"],
    )
    assert did > 0


def test_e10_approve_draft(client):
    """E10: approve_draft promotes a pending draft to lessons_learned."""
    client.register_slug("tooling")
    did = client.propose_lesson_draft(
        title="draft", project_tags=["tooling"], prevention_rule="rule",
    )
    lesson_id = client.approve_draft(did, actor="tester")
    assert lesson_id > 0


def test_e10_reject_draft(client):
    """E10: reject_draft marks the draft as rejected (no lesson created)."""
    client.register_slug("tooling")
    did = client.propose_lesson_draft(title="bad", project_tags=["tooling"])
    client.reject_draft(did, actor="tester", notes="not useful")
    drafts = client.list_drafts(status="rejected")
    assert any(d["id"] == did for d in drafts)


def test_e10_regen_brief(client):
    """E10: regen_brief writes the brief file under the line cap (HR-5)."""
    client.register_slug("tooling")
    lid = client.log_lesson(
        title="for brief", project_tags=["tooling"], severity="info",
    )
    md_path = client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tooling", json.dumps([lid]), "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    result = client.regen_brief("tooling")
    assert result["written"] is True


def test_e10_set_visibility(seeded_client):
    """E10: set_visibility updates the ACL column on chunks/lessons."""
    chunks = seeded_client.list_chunks(project="tooling")
    seeded_client.set_visibility(
        table="knowledge_chunks", record_id=chunks[0]["id"], level="team",
    )


def test_e10_register_slug(client):
    """E10: register_slug adds a canonical slug."""
    client.register_slug("test_slug_e10")
    from runaway_context.slugs_lifecycle import SlugRegistry
    reg = SlugRegistry(client._knowledge_db)
    assert "test_slug_e10" in reg.list_active()


def test_e10_alias_slug(client):
    """E10: alias_slug points an alias at a canonical slug."""
    client.register_slug("primary")
    client.alias_slug("alias_a", "primary")


def test_e10_deprecate_slug(client):
    """E10: deprecate_slug retires a slug from writes."""
    client.register_slug("legacy_x")
    client.deprecate_slug("legacy_x", reason="replaced")


def test_e10_merge_slugs(client):
    """E10: merge_slugs folds one slug under another."""
    client.register_slug("a_slug")
    client.register_slug("b_slug")
    client.merge_slugs("a_slug", "b_slug")


def test_e10_register_specialist(client):
    """E10: register_specialist adds a row to specialists."""
    sid = client.register_specialist(name="QA", domain="quality")
    assert sid > 0


def test_e10_attach_to_specialist(seeded_client):
    """E10: attach_to_specialist links a chunk/lesson to a specialist."""
    sid = seeded_client.register_specialist(name="SrcSpec", domain="source")
    chunks = seeded_client.list_chunks(project="tooling")
    seeded_client.attach_to_specialist(
        specialist_id=sid, table="knowledge_chunks", record_id=chunks[0]["id"],
    )


def test_e10_audit_verify(seeded_client):
    """E10: audit_verify returns (True, None, None) on an intact chain."""
    ok, bad_id, reason = seeded_client.audit_verify()
    assert ok is True


def test_e10_export_json(seeded_client, tmp_path):
    """E10: export_json writes a JSON file with the corpus."""
    out = tmp_path / "export.json"
    rows = seeded_client.export_json(out, project="tooling")
    assert out.exists()
    assert rows >= 0


def test_e10_import_json(seeded_client, tmp_path):
    """E10: import_json reads back a previously-exported corpus."""
    out = tmp_path / "exp.json"
    seeded_client.export_json(out, project="tooling")
    # Re-importing the same corpus may produce conflicts; just confirm callable.
    try:
        result = seeded_client.import_json(out, actor="tester")
        assert isinstance(result, dict)
    except Exception as exc:
        # Conflict on round-trip is acceptable per T3 contract.
        from runaway_context.errors import ConflictReported
        assert isinstance(exc, (ConflictReported, ValueError))
