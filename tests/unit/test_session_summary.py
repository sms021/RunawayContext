"""Unit tests for session_summary.ingest_transcript guardrails.

Each test exercises one of the nine guardrails so a future refactor can't
quietly remove protection. The guardrails exist specifically to prevent a
malfunctioning hook from eating Anthropic token budget; deleting any one is
a HR-12 violation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from runaway_context import budget, session_summary as ss
from runaway_context.config import Config


pytestmark = pytest.mark.feature


def _make_cfg(tmp_path: Path) -> Config:
    install = tmp_path / "install"
    install.mkdir()
    sessions = install / "sessions.db"
    # Minimum schema for session_logs insert
    import sqlite3
    conn = sqlite3.connect(str(sessions))
    try:
        conn.execute(
            "CREATE TABLE session_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "conversation_id TEXT UNIQUE, "
            "tool TEXT, machine TEXT, project_hint TEXT, "
            "started_at DATETIME, ended_at DATETIME, "
            "full_transcript TEXT, token_in INTEGER, token_out INTEGER, "
            "notes TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    cfg = Config(install_dir=install, sessions_db=sessions)
    cfg.summarizer_idle_threshold_sec = 0  # tests run fast — skip idle wait
    return cfg


def _write_transcript(path: Path, conversation_id: str, *, age_sec: float = 0.0) -> Path:
    """Write a minimal JSONL transcript and optionally age its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "sessionId": conversation_id, "cwd": "/some/project",
            "timestamp": "2026-05-15T03:15:00Z",
            "message": {"content": [{"type": "text", "text": "hello world"}]},
        }) + "\n",
        json.dumps({
            "sessionId": conversation_id,
            "timestamp": "2026-05-15T03:16:00Z",
            "message": {"content": [{"type": "text", "text": "goodbye world"}]},
        }) + "\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")
    if age_sec > 0:
        mtime = time.time() - age_sec
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_ingest_parses_and_inserts(tmp_path):
    cfg = _make_cfg(tmp_path)
    t = _write_transcript(tmp_path / "t.jsonl", "conv-1")
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is True
    assert result.conversation_id == "conv-1"
    assert result.used_llm is False  # provider="off" → metadata-only


def test_guard6_processed_marker_skips_second_call(tmp_path):
    cfg = _make_cfg(tmp_path)
    t = _write_transcript(tmp_path / "t.jsonl", "conv-2")
    first = ss.ingest_transcript(cfg, t)
    assert first.inserted is True
    second = ss.ingest_transcript(cfg, t)
    assert second.inserted is False
    assert second.skipped_reason == "already-processed"


def test_guard2_cooldown_blocks_in_flight_retry(tmp_path):
    """A pending marker within cooldown window blocks reingest.

    Cooldown specifically protects against re-entry while another summarizer
    instance is in flight (pending marker present, processed not yet written).
    """
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_cooldown_sec = 3600
    t = _write_transcript(tmp_path / "t.jsonl", "conv-3")
    # Drop a fresh pending marker manually
    ss._write_pending(cfg.install_dir, "conv-3")
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is False
    assert result.skipped_reason == "cooldown"


def test_guard5_idle_threshold_blocks_recent_transcript(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_idle_threshold_sec = 60
    t = _write_transcript(tmp_path / "t.jsonl", "conv-4", age_sec=10)
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is False
    assert result.skipped_reason == "not-idle"


def test_guard7_attempt_cap_marks_permanent_failure(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_attempt_cap = 1
    # Pre-populate attempt counter past cap
    ss._bump_attempt(cfg.install_dir, "conv-5")
    t = _write_transcript(tmp_path / "t.jsonl", "conv-5")
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is False
    assert result.skipped_reason == "attempt-cap-exceeded"
    assert ss._permanent_fail_marker(cfg.install_dir, "conv-5").exists()


def test_guard9_circuit_breaker_halts_summarizer(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_circuit_break_after = 1
    cfg.summarizer_circuit_recovery_sec = 999
    # Trip the circuit by recording a failure
    ss._circuit_record_failure(cfg.install_dir, cfg)
    t = _write_transcript(tmp_path / "t.jsonl", "conv-6")
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is False
    assert result.skipped_reason.startswith("circuit-open-")


def test_guard3_char_cap_truncates_transcript_view(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_char_cap = 50
    parsed = ss.parse_transcript(
        _write_transcript(tmp_path / "t.jsonl", "conv-7"),
        char_cap=cfg.summarizer_char_cap,
    )
    assert len(parsed.text_excerpt) <= cfg.summarizer_char_cap


def test_guard8_budget_skip_when_over_cap_with_provider(tmp_path):
    """When provider is set AND daily cap is exhausted, the row inserts with metadata-only."""
    cfg = _make_cfg(tmp_path)
    cfg.summarizer_provider = "claude-cli"
    cfg.summarizer_daily_token_cap = 1  # exhausted by anything
    t = _write_transcript(tmp_path / "t.jsonl", "conv-8")
    result = ss.ingest_transcript(cfg, t)
    assert result.inserted is True
    assert result.used_llm is False  # budget refused — fall back to metadata
    assert result.tokens_in == 0


def test_force_bypasses_guards(tmp_path):
    cfg = _make_cfg(tmp_path)
    t = _write_transcript(tmp_path / "t.jsonl", "conv-9")
    ss.ingest_transcript(cfg, t)  # first call processes it
    second = ss.ingest_transcript(cfg, t, force=True)
    # force bypasses the processed marker, so the second call also succeeds
    assert second.inserted is True


def test_discover_transcripts_returns_jsonl_only(tmp_path):
    root = tmp_path / "projects"
    (root / "p1").mkdir(parents=True)
    (root / "p1" / "a.jsonl").write_text("{}\n")
    (root / "p1" / "b.txt").write_text("not a transcript")
    found = ss.discover_transcripts([root])
    assert all(p.suffix == ".jsonl" for p in found)
    assert len(found) == 1
