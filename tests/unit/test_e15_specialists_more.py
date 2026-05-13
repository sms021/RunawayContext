"""E15 — specialists — coverage for detach, regen_specialist_md, edge cases."""
from __future__ import annotations

import pytest

from runaway_context.specialists import SpecialistRegistry, _validate_table

pytestmark = pytest.mark.feature


def test_specialist_register_validates(fresh_db):
    reg = SpecialistRegistry(fresh_db)
    with pytest.raises(ValueError):
        reg.register(name="", domain="x")
    with pytest.raises(ValueError):
        reg.register(name="x", domain="")


def test_specialist_register_idempotent_updates_fields(fresh_db):
    """Re-registering by name updates other fields."""
    reg = SpecialistRegistry(fresh_db)
    sid1 = reg.register(name="X", domain="d1")
    sid2 = reg.register(name="X", domain="d2", description="hello",
                        md_path="/tmp/x.md")
    assert sid1 == sid2
    rows = reg.list()
    target = [r for r in rows if r["name"] == "X"][0]
    assert target["domain"] == "d2"
    assert target["description"] == "hello"


def test_specialist_list_all_vs_active(fresh_db):
    reg = SpecialistRegistry(fresh_db)
    reg.register(name="A", domain="d")
    rows_active = reg.list(active_only=True)
    rows_all = reg.list(active_only=False)
    assert any(r["name"] == "A" for r in rows_active)
    assert any(r["name"] == "A" for r in rows_all)


def test_specialist_attach_invalid_table(fresh_db):
    reg = SpecialistRegistry(fresh_db)
    sid = reg.register(name="Y", domain="d")
    with pytest.raises(ValueError):
        reg.attach(specialist_id=sid, table="bad_table", record_id=1)


def test_specialist_detach_round_trip(seeded_client):
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    sid = reg.register(name="Det", domain="d")
    chunks = seeded_client.list_chunks(project="tooling")
    reg.attach(specialist_id=sid, table="knowledge_chunks",
               record_id=chunks[0]["id"])
    assert reg.knowledge_for(sid)["chunks"]
    reg.detach(specialist_id=sid, table="knowledge_chunks",
               record_id=chunks[0]["id"])
    assert not reg.knowledge_for(sid)["chunks"]


def test_specialist_detach_invalid_table(fresh_db):
    reg = SpecialistRegistry(fresh_db)
    sid = reg.register(name="Z", domain="d")
    with pytest.raises(ValueError):
        reg.detach(specialist_id=sid, table="bad", record_id=1)


def test_validate_table_function():
    """Underlying helper rejects unknown tables."""
    with pytest.raises(ValueError):
        _validate_table("not_a_table")
    _validate_table("knowledge_chunks")
    _validate_table("lessons_learned")


def test_regen_specialist_md_writes_to_md_path(seeded_client, tmp_path):
    """regen_specialist_md writes file when md_path is set on the row."""
    md_path = tmp_path / "spec" / "out.md"
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    sid = reg.register(name="MD", domain="d", md_path=str(md_path))
    content = reg.regen_specialist_md(sid)
    assert md_path.exists()
    assert "Specialist: MD" in md_path.read_text()
    assert "Specialist: MD" in content


def test_regen_specialist_md_writes_to_md_dir(seeded_client, tmp_path):
    """When md_dir is set + row has no md_path, file is written under md_dir."""
    md_dir = tmp_path / "specdir"
    reg = SpecialistRegistry(seeded_client._knowledge_db, md_dir=md_dir)
    sid = reg.register(name="DirSpec", domain="d")
    reg.regen_specialist_md(sid)
    assert (md_dir / "DirSpec.md").exists()


def test_regen_specialist_md_no_target_just_returns(seeded_client):
    """When neither md_path nor md_dir is set, returns content without writing."""
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    sid = reg.register(name="NoOut", domain="d")
    content = reg.regen_specialist_md(sid)
    assert "Specialist: NoOut" in content


def test_regen_specialist_md_unknown_id(seeded_client):
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    with pytest.raises(LookupError):
        reg.regen_specialist_md(99999)


def test_regen_specialist_md_with_attachments(seeded_client, tmp_path):
    """Generated MD lists attached lessons and chunks."""
    reg = SpecialistRegistry(seeded_client._knowledge_db,
                             md_dir=tmp_path / "specs")
    sid = reg.register(name="Full", domain="d", description="desc")
    chunks = seeded_client.list_chunks(project="tooling")
    lessons = seeded_client.list_lessons(project="tooling")
    reg.attach(specialist_id=sid, table="knowledge_chunks",
               record_id=chunks[0]["id"])
    reg.attach(specialist_id=sid, table="lessons_learned",
               record_id=lessons[0]["id"])
    content = reg.regen_specialist_md(sid)
    assert "LL#" in content
    assert "src#" in content
    assert "desc" in content
