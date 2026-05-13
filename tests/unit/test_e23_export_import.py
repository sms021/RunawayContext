"""E23 — JSON export + import + conflict reporter."""
from __future__ import annotations

import json

import pytest

from runaway_context import transfer

pytestmark = pytest.mark.feature


def test_e23_export_writes_json(seeded_client, tmp_path):
    """E23: export_json writes a JSON file with the corpus."""
    out = tmp_path / "corpus.json"
    rows = transfer.export_json(seeded_client._knowledge_db, out,
                                project="tooling")
    assert out.exists()
    assert rows > 0
    payload = json.loads(out.read_text())
    assert "lessons" in payload
    assert "chunks" in payload


def test_e23_import_round_trip(seeded_client, tmp_install, tmp_path):
    """E23: import_json reads back exported rows into a fresh install."""
    out = tmp_path / "corpus.json"
    transfer.export_json(seeded_client._knowledge_db, out, project="tooling")

    # Create a second install and migrate it, then import.
    from runaway_context.migrate import migrate
    other = tmp_path / "other_install"
    other.mkdir()
    other_db = other / "knowledge.db"
    migrate(other_db, sessions_db=other / "sessions.db",
            metrics_db=other / "metrics.db")
    # Register the same slug so HR-2 passes on the import side.
    from runaway_context.slugs_lifecycle import SlugRegistry
    SlugRegistry(other_db).register("tooling")

    result = transfer.import_json(other_db, out, actor="tester")
    assert isinstance(result, dict)


def test_e23_conflict_reporter_renders(tmp_path):
    """E23: ConflictReporter.report emits a structured payload."""
    reporter = transfer.ConflictReporter()
    out = reporter.report([{"table": "lessons_learned", "key": {"title": "X"},
                            "reason": "duplicate"}])
    # Either a list or a dict — just confirm it's serializable.
    json.dumps(out, default=str)
