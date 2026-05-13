"""E3 — verify the reference_count signal fires the active→stable rule.

The plan permits ``record_versions`` OR ``last_revisited`` as the
re-engagement signal. v3.0.0 adds a third — ``reference_count`` — so the
maturation engine has a real, per-read counter (incremented by
``Client.get_lesson`` and ``Client.search_lessons``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from runaway_context import Client
from runaway_context.maturation import MaturationEngine
from runaway_context.migrate import migrate


pytestmark = pytest.mark.feature


def _make_install(tmp_path):
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    c = Client(install_dir=tmp_path)
    c.register_slug("tooling")
    return c


def _backdate_lesson(install_dir, lesson_id, days):
    conn = sqlite3.connect(str(install_dir / "knowledge.db"))
    try:
        past = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        # Drop the FTS triggers/etc; just set created_at directly.
        conn.execute(
            "UPDATE lessons_learned SET created_at = ? WHERE id = ?",
            (past, lesson_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_reference_count_increments_on_get(tmp_path):
    """Client.get_lesson increments reference_count on hit."""
    c = _make_install(tmp_path)
    lid = c.log_lesson(title="ref test", project_tags=["tooling"])

    # First fetch increments once.
    c.get_lesson(lid)
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    row = conn.execute(
        "SELECT reference_count FROM lessons_learned WHERE id = ?", (lid,)
    ).fetchone()
    conn.close()
    assert row[0] == 1

    # Three more fetches → 4 total.
    for _ in range(3):
        c.get_lesson(lid)
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    row = conn.execute(
        "SELECT reference_count FROM lessons_learned WHERE id = ?", (lid,)
    ).fetchone()
    conn.close()
    assert row[0] == 4


def test_reference_count_fires_active_to_stable(tmp_path):
    """active→stable fires when reference_count >= 3, even without revisions / last_revisited."""
    c = _make_install(tmp_path)
    lid = c.log_lesson(title="re-engaged via refs", project_tags=["tooling"])
    # Promote scar → active explicitly via the approved path (HR-9).
    c.mature_lesson(lid, to_state="active", actor="tester")
    _backdate_lesson(tmp_path, lid, days=120)

    # Three reads via get_lesson — increments reference_count to 3.
    for _ in range(3):
        c.get_lesson(lid)

    # Clear last_revisited so we know reference_count is the firing signal.
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    conn.execute(
        "UPDATE lessons_learned SET last_revisited = NULL WHERE id = ?", (lid,)
    )
    conn.commit()
    conn.close()

    engine = MaturationEngine(tmp_path / "knowledge.db")
    suggestions = engine.suggest_transitions()
    targeted = [s for s in suggestions if s["lesson_id"] == lid]
    assert len(targeted) == 1, suggestions
    assert targeted[0]["to_state"] == "stable"
    assert "refs=" in targeted[0]["reason"]


def test_reference_count_does_not_fire_below_threshold(tmp_path):
    """A single read is not enough to trigger active→stable."""
    c = _make_install(tmp_path)
    lid = c.log_lesson(title="one read only", project_tags=["tooling"])
    c.mature_lesson(lid, to_state="active", actor="tester")
    _backdate_lesson(tmp_path, lid, days=120)
    c.get_lesson(lid)  # reference_count == 1; last_revisited set

    # Wipe last_revisited so refs=1 is the only signal.
    conn = sqlite3.connect(str(tmp_path / "knowledge.db"))
    conn.execute(
        "UPDATE lessons_learned SET last_revisited = NULL WHERE id = ?", (lid,)
    )
    conn.commit()
    conn.close()

    engine = MaturationEngine(tmp_path / "knowledge.db")
    suggestions = engine.suggest_transitions()
    targeted = [s for s in suggestions if s["lesson_id"] == lid]
    assert targeted == []
