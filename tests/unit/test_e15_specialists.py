"""E15 — specialist agents (register, attach, knowledge_for, coverage_report)."""
from __future__ import annotations

import pytest

from runaway_context.specialists import SpecialistRegistry, coverage_report

pytestmark = pytest.mark.feature


def test_e15_register_specialist(fresh_db):
    """E15: SpecialistRegistry.register adds a row."""
    reg = SpecialistRegistry(fresh_db)
    sid = reg.register(name="DocBot", domain="docs", description="docs spec")
    assert sid > 0


def test_e15_attach_and_knowledge_for(seeded_client):
    """E15: attach + knowledge_for returns the linked rows."""
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    sid = reg.register(name="Knower", domain="tooling")
    chunks = seeded_client.list_chunks(project="tooling")
    reg.attach(specialist_id=sid, table="knowledge_chunks", record_id=chunks[0]["id"])
    payload = reg.knowledge_for(sid)
    assert "knowledge_chunks" in payload or "chunks" in payload
    # The flat list of attached rows
    flat = []
    for v in payload.values():
        if isinstance(v, list):
            flat.extend(v)
    assert flat


def test_e15_coverage_report(seeded_client):
    """E15: coverage_report returns a list of specialist coverage dicts."""
    reg = SpecialistRegistry(seeded_client._knowledge_db)
    reg.register(name="CovSpec", domain="cov")
    rows = coverage_report(seeded_client._knowledge_db)
    assert isinstance(rows, list)
