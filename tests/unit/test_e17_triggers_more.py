"""E17 — triggers — extra branches."""
from __future__ import annotations

import pytest

from runaway_context.triggers import (
    auto_propose,
    detect_triggers,
    suggest_lesson_from_conversation,
    _suggest_severity,
    _normalize,
    _split_sentences,
    _index_to_sentence,
)

pytestmark = pytest.mark.feature


def test_detect_triggers_empty_returns_empty():
    assert detect_triggers("") == []
    assert detect_triggers(None) == []
    assert detect_triggers(123) == []


def test_detect_triggers_finds_weak_only():
    """Text with weak triggers but no strong ones still produces hits."""
    text = "We need to make sure to check before deploying. Going forward."
    out = detect_triggers(text)
    assert all(t["strength"] in ("strong", "weak") for t in out)
    assert any(t["strength"] == "weak" for t in out)


def test_suggest_severity_high_blast_hint():
    out = _suggest_severity("this took down production")
    assert out["blast_radius"] is not None


def test_suggest_severity_multiple_blast_hints():
    """Two-or-more high-blast hints push to 5."""
    out = _suggest_severity("this caused data loss in production")
    assert out["blast_radius"] == 5


def test_suggest_severity_frequency_hints():
    out = _suggest_severity("this keeps happening every time")
    assert out["frequency"] is not None


def test_suggest_severity_reversibility_hints():
    out = _suggest_severity("no rollback possible, data loss")
    assert out["reversibility"] is not None


def test_suggest_severity_no_hints():
    out = _suggest_severity("benign text without alerting keywords")
    assert out == {"blast_radius": None, "frequency": None,
                   "reversibility": None}


def test_normalize_handles_none():
    assert _normalize(None) == ""
    assert _normalize("") == ""
    assert _normalize("HELLO") == "hello"


def test_split_sentences():
    assert _split_sentences("") == []
    sents = _split_sentences("First. Second! Third? Final.")
    assert len(sents) >= 3


def test_index_to_sentence():
    sents = ["First sentence.", "Second sentence.", "Third."]
    full = " ".join(sents)
    # Find char in second sentence
    idx = _index_to_sentence(sents, full, full.find("Second"))
    assert idx == 1


def test_suggest_lesson_strong_trigger_high_confidence():
    text = "We got burned yesterday. The fix was non-obvious."
    out = suggest_lesson_from_conversation(text, conversation_id="cv-1")
    assert out["confidence"] == 1.0
    assert out["source_conversation_ref"] == "cv-1"


def test_suggest_lesson_weak_only_low_confidence():
    """3+ weak triggers → 0.7 confidence."""
    text = ("we should remember this. we need to verify checks. "
            "going forward make sure to be careful with deletes.")
    out = suggest_lesson_from_conversation(text)
    assert out["confidence"] >= 0.7


def test_suggest_lesson_one_weak_low_confidence():
    text = "we need to do better next time"
    out = suggest_lesson_from_conversation(text)
    assert out["confidence"] == 0.3


def test_suggest_lesson_no_match_zero_confidence():
    out = suggest_lesson_from_conversation("nothing interesting here")
    assert out["confidence"] == 0.0
    assert out["title"] is None


def test_auto_propose_requires_client():
    with pytest.raises(AttributeError):
        auto_propose(None, transcript="we got burned",
                     project_tags=["x"])


def test_auto_propose_requires_propose_method():
    class FakeClient:
        pass
    with pytest.raises(AttributeError):
        auto_propose(FakeClient(), transcript="we got burned",
                     project_tags=["x"])


def test_auto_propose_empty_transcript_returns_none(seeded_client):
    out = auto_propose(seeded_client, transcript="",
                       project_tags=["tooling"])
    assert out is None


def test_auto_propose_no_tags_returns_none(seeded_client):
    out = auto_propose(seeded_client, transcript="we got burned by x",
                       project_tags=[])
    assert out is None


def test_auto_propose_below_confidence_returns_none(seeded_client):
    """A single weak trigger doesn't clear the 0.7 bar."""
    out = auto_propose(seeded_client,
                       transcript="we should be careful next time",
                       project_tags=["tooling"])
    assert out is None
