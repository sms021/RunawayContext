"""Tests for the doctor check_slug_orphans drift check (v3.2.0)."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context import doctor
from runaway_context.config import Config

pytestmark = pytest.mark.feature


def _direct_insert(client, table: str, project: str, **kw) -> int:
    """Bypass the Client's HR-2 _guard_write so we can plant orphan rows."""
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        if table == "knowledge_chunks":
            cur = conn.execute(
                "INSERT INTO knowledge_chunks "
                "(project, project_tags, topic, title, body, source) "
                "VALUES (?, ?, ?, ?, ?, 'manual')",
                (project, f'["{project}"]',
                 kw.get("topic", "t"), kw.get("title", "T"), kw.get("body", "b")),
            )
        else:
            cur = conn.execute(
                "INSERT INTO lessons_learned "
                "(project, project_tags, title, prevention_rule, severity, status, source) "
                "VALUES (?, ?, ?, ?, 'warning', 'active', 'manual')",
                (project, f'["{project}"]',
                 kw.get("title", "T"), kw.get("prevention_rule", "r")),
            )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_ok_when_all_slugs_active(client):
    """No orphan rows -> OK."""
    client.register_slug("tooling")
    client.log_lesson(title="t", project_tags=["tooling"])
    cfg = Config.load(client.install_dir)
    finding = doctor.check_slug_orphans(cfg)
    assert finding.level == "ok"


def test_warns_on_chunk_with_unregistered_slug(client):
    """A chunk row with project='ghost' (not in slug_registry) -> WARN."""
    client.register_slug("tooling")  # at least one active so we don't short-circuit
    _direct_insert(client, "knowledge_chunks", "ghost")
    cfg = Config.load(client.install_dir)
    finding = doctor.check_slug_orphans(cfg)
    assert finding.level == "warn"
    assert finding.extra["total_orphans"] == 1
    assert finding.extra["samples"][0]["project"] == "ghost"


def test_warns_on_lesson_with_unregistered_slug(client):
    client.register_slug("tooling")
    _direct_insert(client, "lessons_learned", "abandoned")
    cfg = Config.load(client.install_dir)
    finding = doctor.check_slug_orphans(cfg)
    assert finding.level == "warn"
    assert any(s["table"] == "lessons_learned" for s in finding.extra["samples"])


def test_warns_on_deprecated_slug(client):
    """A slug whose status is 'deprecated' is NOT active -> orphan."""
    client.register_slug("oldproj")
    _direct_insert(client, "knowledge_chunks", "oldproj")
    # Mark as deprecated
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "UPDATE slug_registry SET status='deprecated' WHERE slug='oldproj'"
        )
        conn.commit()
    finally:
        conn.close()
    # Register another so the empty-set short-circuit doesn't fire
    client.register_slug("tooling")
    cfg = Config.load(client.install_dir)
    finding = doctor.check_slug_orphans(cfg)
    assert finding.level == "warn"
    assert any(s["project"] == "oldproj" for s in finding.extra["samples"])


def test_soft_deleted_rows_not_counted(client):
    client.register_slug("tooling")
    rid = _direct_insert(client, "knowledge_chunks", "ghost")
    # Soft-delete the orphan row
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "UPDATE knowledge_chunks SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (rid,),
        )
        conn.commit()
    finally:
        conn.close()
    cfg = Config.load(client.install_dir)
    finding = doctor.check_slug_orphans(cfg)
    assert finding.level == "ok"
