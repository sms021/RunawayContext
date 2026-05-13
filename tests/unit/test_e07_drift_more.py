"""E7 — drift detector — extra coverage for all 5 rules."""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from runaway_context.drift import DriftDetector, run_check, print_report

pytestmark = pytest.mark.feature


def _exec(db_path: Path, sql: str, params=()) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_drift_rule_brief_overrun_fires(seeded_client, tmp_path):
    """rule 1: brief_overrun fires when the md file exceeds md_line_cap."""
    md_path = seeded_client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("line\n" * 200)
    _exec(
        seeded_client._knowledge_db,
        "INSERT OR REPLACE INTO project_context_card "
        "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("tooling", "[]", "[]", "[]", str(md_path), 10),
    )
    findings = run_check(seeded_client._knowledge_db,
                        install_dir=seeded_client.install_dir)
    assert any(f["rule"] == "brief_overrun" for f in findings)


def test_drift_rule_brief_overrun_skips_missing_file(seeded_client):
    """brief_overrun: missing md file → no finding."""
    _exec(
        seeded_client._knowledge_db,
        "INSERT OR REPLACE INTO project_context_card "
        "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("tooling", "[]", "[]", "[]", "/tmp/__does_not_exist__/x.md", 5),
    )
    findings = run_check(seeded_client._knowledge_db)
    assert not any(f["rule"] == "brief_overrun" for f in findings)


def test_drift_rule_stack_overload_fires(seeded_client):
    """rule 2: stack_overload fires when >30 active scar lessons."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        for i in range(35):
            conn.execute(
                "INSERT INTO lessons_learned "
                "(project, title, project_tags, severity, status, maturity) "
                "VALUES (?, ?, ?, 'info', 'active', 'active')",
                ("tooling", f"L{i}", '["tooling"]'),
            )
        conn.commit()
    finally:
        conn.close()
    findings = run_check(db)
    assert any(f["rule"] == "stack_overload" for f in findings)


def test_drift_rule_orphaned_chunks_fires(seeded_client):
    """rule 3: orphaned_chunks fires for un-linked + stale chunks."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        # Find a chunk and back-date it
        cid = conn.execute(
            "SELECT id FROM knowledge_chunks LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE knowledge_chunks SET created_at = ?, updated_at = ? WHERE id = ?",
            (old, old, cid),
        )
        conn.commit()
    finally:
        conn.close()
    findings = run_check(db)
    assert any(f["rule"] == "orphaned_chunks" for f in findings)


def test_drift_rule_unreviewed_drafts_fires(seeded_client):
    """rule 4: unreviewed_drafts fires for >30d pending drafts."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO lesson_drafts "
            "(title, project_tags, proposed_at, proposed_by, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            ("very old draft", '["tooling"]', old, "tester"),
        )
        conn.commit()
    finally:
        conn.close()
    findings = run_check(db)
    assert any(f["rule"] == "unreviewed_drafts" for f in findings)


def test_drift_rule_aging_scars_fires(seeded_client):
    """rule 5: aging_scars fires for scars >30d old."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        old = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        # All seeded lessons get maturity='scar' on insertion — back-date one.
        lid = conn.execute(
            "SELECT id FROM lessons_learned LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE lessons_learned SET created_at = ?, maturity = 'scar', "
            "maturity_changed_at = ? WHERE id = ?",
            (old, old, lid),
        )
        conn.commit()
    finally:
        conn.close()
    findings = run_check(db)
    assert any(f["rule"] == "aging_scars" for f in findings)


def test_drift_print_report_handles_findings(seeded_client):
    """print_report renders findings with severity sort."""
    findings = [
        {"rule": "x", "severity": "info", "target": "t1",
         "message": "msg1", "suggestion": "do this"},
        {"rule": "y", "severity": "critical", "target": "t2",
         "message": "msg2", "suggestion": "do that"},
    ]
    buf = io.StringIO()
    print_report(findings, stream=buf)
    text = buf.getvalue()
    # critical sorts before info
    assert text.index("CRITICAL") < text.index("INFO")


def test_drift_print_report_no_findings():
    """print_report on empty findings prints the "no drift" message."""
    buf = io.StringIO()
    print_report([], stream=buf)
    assert "No drift detected" in buf.getvalue()


def test_drift_resolve_md_path_relative(tmp_path):
    """_resolve_md_path anchors a relative path under install_dir."""
    install = tmp_path / "install"
    install.mkdir()
    det = DriftDetector(install / "knowledge.db", install_dir=install)
    out = det._resolve_md_path("briefs/x/CLAUDE.md")
    assert install in out.parents


def test_drift_resolve_md_path_absolute(tmp_path):
    """_resolve_md_path returns absolute paths unchanged."""
    install = tmp_path / "install"
    install.mkdir()
    det = DriftDetector(install / "knowledge.db", install_dir=install)
    abs_path = tmp_path / "elsewhere.md"
    out = det._resolve_md_path(str(abs_path))
    assert out == abs_path


def test_drift_count_file_lines_missing(tmp_path):
    """_count_file_lines returns 0 for unreadable files."""
    out = DriftDetector._count_file_lines(tmp_path / "no_such.md")
    assert out == 0
