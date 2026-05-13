"""HR-2 contract tests — every write requires a valid project slug.

HR-2: ``log_lesson``, ``propose_knowledge``, and ``propose_lesson_draft`` all
validate ``project_tags`` against the slug registry. The DB has triggers as a
secondary guard.
"""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.errors import InvalidProjectSlug
from runaway_context.mcp_server import dispatch

pytestmark = pytest.mark.contract


def test_hr_02_log_lesson_unregistered_slug_rejected(client) -> None:
    """HR-2: log_lesson refuses an unregistered slug."""
    with pytest.raises(InvalidProjectSlug):
        client.log_lesson(
            title="bad slug",
            project_tags=["definitely_not_registered_slug"],
            severity="info",
        )


def test_hr_02_log_lesson_empty_tags_rejected(client) -> None:
    """HR-2: log_lesson refuses empty project_tags."""
    with pytest.raises(InvalidProjectSlug):
        client.log_lesson(title="empty tags", project_tags=[], severity="info")


def test_hr_02_propose_knowledge_invalid_slug_rejected(client) -> None:
    """HR-2: propose_knowledge refuses an unregistered project slug."""
    with pytest.raises(InvalidProjectSlug):
        client.propose_knowledge(
            project="never_registered",
            topic="t",
            title="t",
            body="b",
        )


def test_hr_02_direct_sql_empty_tags_rejected(fresh_db) -> None:
    """HR-2: DB trigger refuses INSERT with empty/null project_tags."""
    conn = sqlite3.connect(str(fresh_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lessons_learned (title, project_tags) VALUES (?, ?)",
                ("bypass attempt", "[]"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO lessons_learned (title, project_tags) VALUES (?, ?)",
                ("bypass attempt", None),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO knowledge_chunks (project, topic, title, body, project_tags) "
                "VALUES (?, ?, ?, ?, ?)",
                ("x", "t", "t", "b", "[]"),
            )
    finally:
        conn.close()


def test_hr_02_mcp_propose_lesson_draft_validates_tags(client, tmp_install) -> None:
    """HR-2: MCP propose_lesson_draft refuses empty project_tags."""
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "propose_lesson_draft",
            "arguments": {"title": "no tags", "project_tags": []},
        },
    }
    response = dispatch(msg, install_dir=tmp_install)
    assert response is not None
    result = response.get("result") or {}
    assert result.get("isError") is True, response
    text = " ".join(c.get("text", "") for c in result.get("content", []))
    assert "project_tags" in text.lower() or "hr-2" in text.lower()
