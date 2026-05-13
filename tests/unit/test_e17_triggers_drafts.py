"""E17 — trigger-based capture: never writes to lessons_learned directly (HR-3)."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context.triggers import (
    STRONG_TRIGGERS, WEAK_TRIGGERS, auto_propose,
    detect_triggers, suggest_lesson_from_conversation,
)

pytestmark = pytest.mark.feature


def test_e17_strong_triggers_present():
    """E17: STRONG_TRIGGERS is non-empty and contains known phrases."""
    assert STRONG_TRIGGERS
    assert "we got burned" in STRONG_TRIGGERS or "lesson learned" in STRONG_TRIGGERS


def test_e17_weak_triggers_present():
    """E17: WEAK_TRIGGERS is non-empty."""
    assert WEAK_TRIGGERS


def test_e17_detect_triggers_finds_strong():
    """E17: detect_triggers locates a strong phrase in free text."""
    text = "Yesterday we got burned by the cache invalidation bug."
    out = detect_triggers(text)
    assert any(t["strength"] == "strong" for t in out)


def test_e17_suggest_lesson_zero_confidence_on_empty():
    """E17: empty transcript yields zero-confidence stub."""
    out = suggest_lesson_from_conversation("")
    assert out["confidence"] == 0.0


def test_e17_auto_propose_writes_to_drafts_not_lessons(seeded_client):
    """E17 + HR-3: auto_propose writes only to lesson_drafts."""
    transcript = (
        "We got burned by the silent failure in the importer. "
        "Going forward we need to log every refusal. The fix was non-obvious."
    )
    draft_id = auto_propose(
        seeded_client, transcript=transcript,
        project_tags=["tooling"],
        conversation_id="conv-1",
    )
    assert draft_id is not None
    # Confirm the row landed in lesson_drafts (status pending), NOT in
    # lessons_learned with no audit.
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        row = conn.execute(
            "SELECT status FROM lesson_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        assert row[0] == "pending"
    finally:
        conn.close()


def test_e17_auto_propose_returns_none_without_triggers(seeded_client):
    """E17: auto_propose returns None when the transcript has no triggers."""
    out = auto_propose(
        seeded_client, transcript="hello world how are you",
        project_tags=["tooling"],
    )
    assert out is None
