"""E23 — transfer — extra coverage for conflict modes, validation, edge cases."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runaway_context import transfer
from runaway_context.errors import ConflictReported, InvalidProjectSlug
from runaway_context.migrate import migrate
from runaway_context.slugs_lifecycle import SlugRegistry

pytestmark = pytest.mark.feature


def _fresh_install(tmp_path: Path, slug: str = "tooling") -> Path:
    install = tmp_path / "install_extra"
    install.mkdir(parents=True, exist_ok=True)
    migrate(install / "knowledge.db",
            sessions_db=install / "sessions.db",
            metrics_db=install / "metrics.db")
    SlugRegistry(install / "knowledge.db").register(slug)
    return install


def test_transfer_on_conflict_invalid(seeded_client, tmp_path):
    """import_json rejects unknown on_conflict modes."""
    out = tmp_path / "out.json"
    transfer.export_json(seeded_client._knowledge_db, out, project="tooling")
    with pytest.raises(ValueError):
        transfer.import_json(seeded_client._knowledge_db, out,
                             actor="tester", on_conflict="bogus")


def test_transfer_import_missing_file(tmp_install):
    """import_json raises FileNotFoundError for missing input."""
    db = tmp_install / "knowledge.db"
    migrate(db)
    SlugRegistry(db).register("tooling")
    with pytest.raises(FileNotFoundError):
        transfer.import_json(db, tmp_install / "nope.json", actor="tester")


def test_transfer_import_payload_must_be_object(tmp_path):
    """import_json rejects non-dict payloads."""
    install = _fresh_install(tmp_path)
    bad = install / "bad.json"
    bad.write_text(json.dumps(["not", "a", "dict"]))
    with pytest.raises(ValueError):
        transfer.import_json(install / "knowledge.db", bad, actor="tester")


def test_transfer_import_no_registered_slugs(tmp_path):
    """import_json refuses when slug_registry is empty (HR-2)."""
    install = tmp_path / "noslugs"
    install.mkdir()
    migrate(install / "knowledge.db")
    # Don't register any slug
    src_install = _fresh_install(tmp_path / "alt", "tooling")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps({"schema_version": "3.0.0",
                                       "lessons": [], "chunks": []}))
    with pytest.raises(InvalidProjectSlug):
        transfer.import_json(install / "knowledge.db", bundle_path,
                             actor="tester")


def test_transfer_import_skip_conflict(seeded_client, tmp_path):
    """on_conflict='skip' increments skipped without raising."""
    src_export = tmp_path / "exp.json"
    transfer.export_json(seeded_client._knowledge_db, src_export,
                         project="tooling")
    payload = json.loads(src_export.read_text())

    # Build a 2nd install with a duplicate-but-different lesson
    other = _fresh_install(tmp_path)
    other_db = other / "knowledge.db"
    # Pre-insert a lesson with same title
    seed_lesson = payload["lessons"][0]
    conn = sqlite3.connect(str(other_db))
    try:
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, what_happened, project_tags, severity, status, maturity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tooling", seed_lesson["title"], "DIFFERENT what_happened",
             '["tooling"]', "info", "active", "active"),
        )
        conn.commit()
    finally:
        conn.close()

    result = transfer.import_json(other_db, src_export,
                                  actor="tester", on_conflict="skip")
    assert result["skipped"] >= 1
    assert result["conflicts"]


def test_transfer_import_overwrite_conflict(seeded_client, tmp_path):
    """on_conflict='overwrite' updates the local row."""
    src_export = tmp_path / "exp.json"
    transfer.export_json(seeded_client._knowledge_db, src_export,
                         project="tooling")
    payload = json.loads(src_export.read_text())

    other = _fresh_install(tmp_path)
    other_db = other / "knowledge.db"
    seed_lesson = payload["lessons"][0]
    conn = sqlite3.connect(str(other_db))
    try:
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, what_happened, project_tags, severity, status, maturity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tooling", seed_lesson["title"], "ORIGINAL what_happened",
             '["tooling"]', "info", "active", "active"),
        )
        conn.commit()
    finally:
        conn.close()

    result = transfer.import_json(other_db, src_export,
                                  actor="tester", on_conflict="overwrite")
    assert result["added"] >= 1


def test_transfer_import_report_raises(seeded_client, tmp_path):
    """on_conflict='report' raises ConflictReported when conflicts found."""
    src_export = tmp_path / "exp.json"
    transfer.export_json(seeded_client._knowledge_db, src_export,
                         project="tooling")
    payload = json.loads(src_export.read_text())

    other = _fresh_install(tmp_path)
    other_db = other / "knowledge.db"
    seed_lesson = payload["lessons"][0]
    conn = sqlite3.connect(str(other_db))
    try:
        conn.execute(
            "INSERT INTO lessons_learned "
            "(project, title, what_happened, project_tags, severity, status, maturity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tooling", seed_lesson["title"], "DIFFERENT what_happened",
             '["tooling"]', "info", "active", "active"),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ConflictReported):
        transfer.import_json(other_db, src_export, actor="tester")


def test_transfer_import_bad_slugs(tmp_path):
    """import_json reports rows whose slugs are unregistered."""
    install = _fresh_install(tmp_path)
    bundle = {
        "schema_version": "3.0.0",
        "lessons": [{
            "title": "bad-slug lesson",
            "project_tags": ["UNKNOWN"],  # not registered
            "severity": "info",
            "what_happened": "x",
        }],
        "chunks": [],
    }
    bpath = tmp_path / "bundle.json"
    bpath.write_text(json.dumps(bundle))
    with pytest.raises(InvalidProjectSlug):
        transfer.import_json(install / "knowledge.db", bpath, actor="tester")


def test_transfer_import_chunks_unknown_slug(tmp_path):
    install = _fresh_install(tmp_path)
    bundle = {
        "schema_version": "3.0.0",
        "lessons": [],
        "chunks": [{
            "project": "unknown",
            "project_tags": ["unknown_proj"],
            "title": "title", "topic": "topic", "body": "body",
        }],
    }
    bpath = tmp_path / "bundle.json"
    bpath.write_text(json.dumps(bundle))
    with pytest.raises(InvalidProjectSlug):
        transfer.import_json(install / "knowledge.db", bpath, actor="tester")


def test_transfer_import_specialists_and_data_sources(tmp_path):
    install = _fresh_install(tmp_path)
    bundle = {
        "schema_version": "3.0.0",
        "lessons": [],
        "chunks": [],
        "specialists": [
            {"name": "Bot1", "domain": "x"},
            {"name": ""},  # ignored
        ],
        "data_sources": [
            {"system": "vista", "name": "table1", "kind": "table"},
            {"system": "", "name": "no-system"},  # ignored
            {"system": "monday", "name": ""},  # ignored
        ],
    }
    bpath = tmp_path / "bundle.json"
    bpath.write_text(json.dumps(bundle))
    result = transfer.import_json(install / "knowledge.db", bpath,
                                  actor="tester")
    # 1 specialist + 1 data_source = 2 added; rest skipped
    assert result["added"] == 2
    assert result["skipped"] == 3


def test_transfer_import_specialists_idempotent(tmp_path):
    """Importing the same specialist twice → second time skipped."""
    install = _fresh_install(tmp_path)
    bundle = {"schema_version": "3.0.0", "lessons": [], "chunks": [],
              "specialists": [{"name": "X", "domain": "y"}]}
    bpath = tmp_path / "b.json"
    bpath.write_text(json.dumps(bundle))
    r1 = transfer.import_json(install / "knowledge.db", bpath, actor="t")
    r2 = transfer.import_json(install / "knowledge.db", bpath, actor="t")
    assert r1["added"] == 1
    assert r2["skipped"] == 1


def test_transfer_import_data_sources_idempotent(tmp_path):
    install = _fresh_install(tmp_path)
    bundle = {"schema_version": "3.0.0", "lessons": [], "chunks": [],
              "data_sources": [{"system": "vista", "name": "JCCD",
                                "kind": "table"}]}
    bpath = tmp_path / "b.json"
    bpath.write_text(json.dumps(bundle))
    transfer.import_json(install / "knowledge.db", bpath, actor="t")
    r2 = transfer.import_json(install / "knowledge.db", bpath, actor="t")
    assert r2["skipped"] == 1


def test_transfer_export_include_archived(seeded_client, tmp_path):
    """include_archived=True exposes archived lessons too."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE lessons_learned SET maturity = 'archived' "
            "WHERE id = (SELECT id FROM lessons_learned LIMIT 1)"
        )
        conn.commit()
    finally:
        conn.close()
    out = tmp_path / "exp.json"
    transfer.export_json(db, out, project="tooling", include_archived=True)
    payload = json.loads(out.read_text())
    statuses = [l.get("maturity") for l in payload["lessons"]]
    assert "archived" in statuses


def test_conflict_reporter_writes_file(tmp_path):
    """ConflictReporter.report writes a file when output_path is provided."""
    reporter = transfer.ConflictReporter()
    out_md = tmp_path / "report.md"
    text = reporter.report(
        [{"table": "lessons_learned", "key": {"title": "X"},
          "local": {"id": 1}, "incoming": {"id": 2},
          "reason": "diff"}],
        output_path=out_md,
    )
    assert out_md.exists()
    assert "Import Conflict Report" in text
    assert "diff" in text


def test_conflict_reporter_empty_input():
    """Empty conflict list still produces a header."""
    out = transfer.ConflictReporter().report([])
    assert "No conflicts" in out


def test_project_tags_set_parses_variants():
    assert transfer._project_tags_set(["a", "b", 5]) == ["a", "b"]
    assert transfer._project_tags_set('["x","y"]') == ["x", "y"]
    assert transfer._project_tags_set("not json") == []
    assert transfer._project_tags_set("") == []
    assert transfer._project_tags_set('{"obj": 1}') == []
