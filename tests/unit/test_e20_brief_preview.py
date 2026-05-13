"""E20 — brief preview + snapshot + rollback."""
from __future__ import annotations

import json
import sqlite3

import pytest

from runaway_context import brief_preview

pytestmark = pytest.mark.feature


def _wire_card(client, project="tooling"):
    """Create a project_context_card pointing at a real md_path."""
    md_path = client.install_dir / "briefs" / project / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# tooling\nseed content\n")
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project, "[]", "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    return md_path


def test_e20_preview_returns_content(seeded_client):
    """E20: preview returns a dict with content/line_count/cap."""
    _wire_card(seeded_client)
    out = brief_preview.preview(seeded_client.install_dir, "tooling")
    assert "content" in out
    assert "line_count" in out


def test_e20_snapshot_and_list(seeded_client):
    """E20: snapshot writes a brief_snapshots row, list_snapshots returns it."""
    _wire_card(seeded_client)
    snap_id = brief_preview.snapshot(seeded_client.install_dir, "tooling",
                                     note="E20 snapshot")
    rows = brief_preview.list_snapshots(seeded_client.install_dir, "tooling")
    assert any(r["id"] == snap_id for r in rows)


def test_e20_rollback_round_trip(seeded_client):
    """E20: rollback restores prior content."""
    md_path = _wire_card(seeded_client)
    snap_id = brief_preview.snapshot(seeded_client.install_dir, "tooling")
    md_path.write_text("# tooling\noverwritten\n")
    out = brief_preview.rollback(
        seeded_client.install_dir, "tooling",
        snapshot_id=snap_id, actor="tester",
    )
    assert md_path.read_text() == "# tooling\nseed content\n"
    assert out["restored_from_id"] == snap_id
