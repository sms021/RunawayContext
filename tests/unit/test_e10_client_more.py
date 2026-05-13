"""E10 — Client API — extra coverage for error / edge branches."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runaway_context.client import Client
from runaway_context.config import Config
from runaway_context.errors import (
    InvalidProjectSlug,
    MaturationApprovalRequired,
    NetworkEgressBlocked,
    RunawayContextError,
)

pytestmark = pytest.mark.feature


def test_client_refuses_unmigrated_db(tmp_path):
    """Client construction fails when knowledge.db is missing."""
    with pytest.raises(RunawayContextError):
        Client(install_dir=tmp_path)


def test_client_refuses_old_schema(tmp_install):
    """Client refuses when schema_version major != 3."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db",
            sessions_db=tmp_install / "sessions.db",
            metrics_db=tmp_install / "metrics.db")
    conn = sqlite3.connect(str(tmp_install / "knowledge.db"))
    try:
        conn.execute("UPDATE schema_version SET major = 2 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RunawayContextError):
        Client(install_dir=tmp_install)


def test_client_log_lesson_empty_title(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.log_lesson(title="", project_tags=["tooling"])


def test_client_log_lesson_bad_severity(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.log_lesson(title="x", project_tags=["tooling"],
                                 severity="bogus")


def test_client_log_lesson_bad_axes(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.log_lesson(title="x", project_tags=["tooling"],
                                 blast_radius=99)


def test_client_log_lesson_empty_tags(seeded_client):
    with pytest.raises(InvalidProjectSlug):
        seeded_client.log_lesson(title="x", project_tags=[])


def test_client_log_lesson_unknown_slug(seeded_client):
    with pytest.raises(InvalidProjectSlug):
        seeded_client.log_lesson(title="x", project_tags=["never_seen"])


def test_client_propose_knowledge_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.propose_knowledge(project="tooling", topic="",
                                        title="t", body="b")
    with pytest.raises(ValueError):
        seeded_client.propose_knowledge(project="tooling", topic="x",
                                        title="", body="b")
    with pytest.raises(ValueError):
        seeded_client.propose_knowledge(project="tooling", topic="x",
                                        title="t", body="")


def test_client_supersede_self_raises(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.supersede(old_lesson_id=1, new_lesson_id=1)


def test_client_supersede_missing(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.supersede(old_lesson_id=99999, new_lesson_id=1)


def test_client_soft_delete_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.soft_delete(table="bad", record_id=1, actor="me",
                                  reason="r")
    with pytest.raises(ValueError):
        seeded_client.soft_delete(table="lessons_learned", record_id=1,
                                  actor="", reason="r")
    with pytest.raises(ValueError):
        seeded_client.soft_delete(table="lessons_learned", record_id=1,
                                  actor="me", reason="")
    with pytest.raises(ValueError):
        seeded_client.soft_delete(table="lessons_learned", record_id=99999,
                                  actor="me", reason="r")


def test_client_mature_lesson_requires_actor(seeded_client):
    with pytest.raises(MaturationApprovalRequired):
        seeded_client.mature_lesson(lesson_id=1, to_state="stable", actor="")


def test_client_propose_draft_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.propose_lesson_draft(title="", project_tags=["tooling"])


def test_client_approve_draft_no_actor(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.approve_draft(draft_id=1, actor="")


def test_client_approve_draft_missing(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.approve_draft(draft_id=99999, actor="me")


def test_client_reject_draft_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.reject_draft(draft_id=1, actor="")
    with pytest.raises(ValueError):
        seeded_client.reject_draft(draft_id=99999, actor="me")


def test_client_set_visibility_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.set_visibility(table="bad", record_id=1, level="private")
    with pytest.raises(ValueError):
        seeded_client.set_visibility(table="lessons_learned", record_id=1,
                                     level="bogus")
    with pytest.raises(ValueError):
        seeded_client.set_visibility(table="lessons_learned", record_id=99999,
                                     level="team")


def test_client_set_visibility_ok(seeded_client):
    lessons = seeded_client.list_lessons(project="tooling")
    seeded_client.set_visibility(table="lessons_learned",
                                 record_id=lessons[0]["id"], level="team")


def test_client_register_specialist_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.register_specialist(name="", domain="x")
    with pytest.raises(ValueError):
        seeded_client.register_specialist(name="x", domain="")


def test_client_attach_specialist_validation(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.attach_to_specialist(specialist_id=1, table="bad",
                                           record_id=1)
    with pytest.raises(ValueError):
        seeded_client.attach_to_specialist(specialist_id=99999,
                                           table="lessons_learned",
                                           record_id=1)


def test_client_attach_unknown_record(seeded_client):
    sid = seeded_client.register_specialist(name="Sp", domain="d")
    with pytest.raises(ValueError):
        seeded_client.attach_to_specialist(specialist_id=sid,
                                           table="lessons_learned",
                                           record_id=99999)


def test_client_import_requires_actor(seeded_client, tmp_path):
    out = tmp_path / "x.json"
    out.write_text(json.dumps({"lessons": [], "chunks": []}))
    with pytest.raises(ValueError):
        seeded_client.import_json(input_path=out, actor="")


def test_client_stats_works(seeded_client):
    """stats() delegates to stats module."""
    out = seeded_client.stats()
    assert "lessons_total" in out or "lessons_active" in out


def test_client_tier_check(seeded_client):
    out = seeded_client.tier_check()
    assert "tier" in out


def test_client_list_drafts_invalid_status(seeded_client):
    with pytest.raises(ValueError):
        seeded_client.list_drafts(status="bogus")


def test_client_list_drafts_all(seeded_client):
    out = seeded_client.list_drafts(status="all")
    assert isinstance(out, list)


def test_client_get_brief_missing_project(seeded_client):
    from runaway_context.errors import ProjectNotFound
    with pytest.raises((ValueError, ProjectNotFound)):
        seeded_client.get_brief("never_registered_card")


def test_client_get_chunk_get_lesson(seeded_client):
    chunks = seeded_client.list_chunks(project="tooling")
    out = seeded_client.get_chunk(chunks[0]["id"])
    assert out["id"] == chunks[0]["id"]
    lessons = seeded_client.list_lessons(project="tooling")
    out = seeded_client.get_lesson(lessons[0]["id"])
    assert out["id"] == lessons[0]["id"]
    # missing -> None
    assert seeded_client.get_chunk(999999) is None
    assert seeded_client.get_lesson(999999) is None


def test_client_hr1_self_check_refuses(tmp_install, monkeypatch):
    """If a network module is imported but its flag is False, refuse."""
    from runaway_context.migrate import migrate
    migrate(tmp_install / "knowledge.db",
            sessions_db=tmp_install / "sessions.db",
            metrics_db=tmp_install / "metrics.db")
    import sys
    import types
    fake = types.ModuleType("runaway_context.embeddings.providers.openai")
    monkeypatch.setitem(sys.modules,
                        "runaway_context.embeddings.providers.openai", fake)
    cfg = Config.load(tmp_install)
    cfg.embeddings_enabled = False
    with pytest.raises(NetworkEgressBlocked):
        Client(install_dir=tmp_install, config=cfg)


def test_client_register_slug_invalid_format(seeded_client):
    """register_slug raises InvalidProjectSlug for bad format."""
    with pytest.raises(InvalidProjectSlug):
        seeded_client.register_slug("Bad Slug!")
