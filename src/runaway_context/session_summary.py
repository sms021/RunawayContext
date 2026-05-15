"""Session transcript summarizer with full server-pattern guardrails.

A v3 install captures every Claude conversation into ``sessions.db.session_logs``
so prior work is queryable. The pipeline runs in three modes:

- **Stop-hook** (CLI): ``bin/capture_session.sh`` is registered in
  ``~/.claude/settings.json``; it runs ``runaway sessions ingest --transcript
  <path>`` for each transcript when Claude Code stops.
- **Cron** (IDE extensions): ``bin/watch_sessions.sh`` runs every 10 minutes
  via cron and pulls any transcripts the Stop hook missed (VS Code's Claude
  extension does not fire Stop hooks).
- **CLI** (one-shot): ``runaway sessions ingest --transcript <path>`` for
  manual catch-up.

All three modes go through :func:`ingest_transcript`, which enforces the
guardrails listed below. The guards exist to prevent a malfunctioning hook
or wedged summarizer from burning through Anthropic API credit. The cost of
a token-budget exhaustion incident in production was the reason these were
made first-class: see CHANGELOG [3.1.0] and LL on the install incident
2026-05-13.

Guardrails (all enforced inside :func:`ingest_transcript`):

1. **Global lock** with timeout — only one summarizer runs at a time.
2. **Per-conversation cooldown** — same conv cannot be summarized within
   ``summarizer_cooldown_sec`` (default 300s).
3. **Char cap** — transcripts truncated to ``summarizer_char_cap`` bytes
   (default 30_000) before any model call.
4. **Pending marker** — atomic in-progress flag, removed on done OR fail.
5. **Idle threshold** — only summarize sessions whose transcript hasn't
   grown in ``summarizer_idle_threshold_sec`` seconds (default 1800).
6. **Processed marker** — once a conversation is in session_logs, the
   watcher skips it forever (no double-write).
7. **Attempt cap** — after ``summarizer_attempt_cap`` failures (default 3),
   the conversation is marked permanently-failed and never retried.
8. **Daily token budget** — :mod:`runaway_context.budget` ledger refuses
   model calls past ``summarizer_daily_token_cap`` (default 50_000); the
   summarizer falls back to metadata-only inserts.
9. **Circuit breaker** — after
   ``summarizer_circuit_break_after`` consecutive failures (default 5),
   the entire summarizer halts for ``summarizer_circuit_recovery_sec``
   (default 3600) and logs loudly.

HR-1: this module makes ZERO network calls when
``summarizer_provider`` is ``"off"`` (the default). Provider plug-ins are
gated by config and live in submodules with their own allowlist entries.

HR-8: every guard's failure path is fire-and-forget — guards never raise
into the calling Stop hook because that would block Claude.
"""

from __future__ import annotations

import dataclasses
import datetime
import errno
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from runaway_context.budget import check_and_reserve, record_failure, record_usage
from runaway_context.config import Config


# ---------------------------------------------------------------------------
# Filesystem layout under <install_dir>/sessions_state/
# ---------------------------------------------------------------------------


def _state_dir(install_dir: Path) -> Path:
    """Return ``<install_dir>/sessions_state/`` (created on demand)."""
    p = Path(install_dir) / "sessions_state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_id(conversation_id: str) -> str:
    """Strip filesystem-unsafe chars from a conversation id."""
    return "".join(c if c.isalnum() or c in ".-_" else "_" for c in conversation_id)[:128]


def _processed_marker(install_dir: Path, conversation_id: str) -> Path:
    return _state_dir(install_dir) / f"{_safe_id(conversation_id)}.processed"


def _pending_marker(install_dir: Path, conversation_id: str) -> Path:
    return _state_dir(install_dir) / f"{_safe_id(conversation_id)}.pending"


def _attempt_marker(install_dir: Path, conversation_id: str) -> Path:
    return _state_dir(install_dir) / f"{_safe_id(conversation_id)}.attempts"


def _permanent_fail_marker(install_dir: Path, conversation_id: str) -> Path:
    return _state_dir(install_dir) / f"{_safe_id(conversation_id)}.failed_permanent"


def _circuit_state_path(install_dir: Path) -> Path:
    return _state_dir(install_dir) / "_circuit.json"


def _lock_path(install_dir: Path) -> Path:
    return _state_dir(install_dir) / "_summarizer.lock"


# ---------------------------------------------------------------------------
# Guard 1: global lock with timeout
# ---------------------------------------------------------------------------


@contextmanager
def _global_lock(install_dir: Path, timeout_sec: int) -> Iterator[bool]:
    """Acquire the global summarizer lock or yield ``False`` after timeout.

    Yields:
        True if the lock was acquired; False otherwise. The caller is
        responsible for short-circuiting when False is yielded.

    Refuses:
        Nothing — the timeout is enforced, not raised. Callers fall back to
        a no-op rather than block the Stop hook.
    """
    path = _lock_path(install_dir)
    path.touch(exist_ok=True)
    fd = os.open(str(path), os.O_RDWR)
    deadline = time.time() + max(1, timeout_sec)
    acquired = False
    try:
        while time.time() < deadline:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                time.sleep(0.5)
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # HR-8: best-effort unlock
        os.close(fd)


# ---------------------------------------------------------------------------
# Guard 9: circuit breaker
# ---------------------------------------------------------------------------


def _circuit_state(install_dir: Path) -> Dict[str, Any]:
    """Read the current circuit state (consecutive_failures + halted_until)."""
    p = _circuit_state_path(install_dir)
    if not p.exists():
        return {"consecutive_failures": 0, "halted_until": 0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"consecutive_failures": 0, "halted_until": 0}


def _circuit_write(install_dir: Path, state: Dict[str, Any]) -> None:
    p = _circuit_state_path(install_dir)
    try:
        p.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # HR-8: telemetry only


def _circuit_is_open(install_dir: Path) -> Tuple[bool, int]:
    """Return (is_open, seconds_until_recovery). is_open=True means halt."""
    s = _circuit_state(install_dir)
    now = int(time.time())
    halted_until = int(s.get("halted_until", 0))
    if halted_until and now < halted_until:
        return True, halted_until - now
    return False, 0


def _circuit_record_success(install_dir: Path) -> None:
    """Reset the failure counter on any successful summarization."""
    _circuit_write(install_dir, {"consecutive_failures": 0, "halted_until": 0})


def _circuit_record_failure(install_dir: Path, cfg: Config) -> bool:
    """Bump the failure counter; trip the breaker if threshold crossed.

    Returns:
        True if the breaker tripped on this call (was closed, now open).
    """
    s = _circuit_state(install_dir)
    s["consecutive_failures"] = int(s.get("consecutive_failures", 0)) + 1
    tripped = False
    if s["consecutive_failures"] >= max(1, cfg.summarizer_circuit_break_after):
        s["halted_until"] = int(time.time()) + max(60, cfg.summarizer_circuit_recovery_sec)
        s["consecutive_failures"] = 0
        tripped = True
    _circuit_write(install_dir, s)
    return tripped


# ---------------------------------------------------------------------------
# Guards 2/5/6/7: per-conversation markers
# ---------------------------------------------------------------------------


def _is_processed(install_dir: Path, conversation_id: str) -> bool:
    return _processed_marker(install_dir, conversation_id).exists()


def _is_permanently_failed(install_dir: Path, conversation_id: str) -> bool:
    return _permanent_fail_marker(install_dir, conversation_id).exists()


def _within_cooldown(install_dir: Path, conversation_id: str, cooldown_sec: int) -> bool:
    """True iff the conversation was processed (or pending) inside the cooldown window."""
    proc = _processed_marker(install_dir, conversation_id)
    if proc.exists():
        try:
            age = time.time() - proc.stat().st_mtime
        except OSError:
            return False
        return age < max(0, cooldown_sec)
    pending = _pending_marker(install_dir, conversation_id)
    if pending.exists():
        try:
            age = time.time() - pending.stat().st_mtime
        except OSError:
            return False
        return age < max(0, cooldown_sec)
    return False


def _attempt_count(install_dir: Path, conversation_id: str) -> int:
    p = _attempt_marker(install_dir, conversation_id)
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _bump_attempt(install_dir: Path, conversation_id: str) -> int:
    n = _attempt_count(install_dir, conversation_id) + 1
    try:
        _attempt_marker(install_dir, conversation_id).write_text(str(n), encoding="utf-8")
    except OSError:
        pass  # HR-8: marker is advisory
    return n


def _write_pending(install_dir: Path, conversation_id: str) -> None:
    try:
        _pending_marker(install_dir, conversation_id).write_text(
            str(int(time.time())), encoding="utf-8"
        )
    except OSError:
        pass  # HR-8


def _clear_pending(install_dir: Path, conversation_id: str) -> None:
    p = _pending_marker(install_dir, conversation_id)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass  # HR-8


def _mark_processed(install_dir: Path, conversation_id: str) -> None:
    try:
        _processed_marker(install_dir, conversation_id).write_text(
            str(int(time.time())), encoding="utf-8"
        )
    except OSError:
        pass  # HR-8


def _mark_permanent_failure(
    install_dir: Path, conversation_id: str, reason: str
) -> None:
    try:
        _permanent_fail_marker(install_dir, conversation_id).write_text(
            reason, encoding="utf-8"
        )
    except OSError:
        pass  # HR-8


# ---------------------------------------------------------------------------
# Guard 5: idle threshold
# ---------------------------------------------------------------------------


def _is_idle(transcript_path: Path, idle_sec: int) -> bool:
    """True iff the transcript file hasn't been modified within idle_sec seconds."""
    try:
        mtime = transcript_path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) >= max(0, idle_sec)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ParsedTranscript:
    """Lightweight extraction of a Claude Code JSONL transcript."""

    conversation_id: str
    started_at: Optional[str]
    ended_at: Optional[str]
    message_count: int
    cwd: Optional[str]
    text_excerpt: str
    full_size: int


def parse_transcript(path: Path, char_cap: int = 30_000) -> ParsedTranscript:
    """Parse a Claude Code JSONL transcript.

    The Claude Code transcript format is one JSON object per line. We pull
    the first/last timestamps, message count, working dir, and a truncated
    text view (text_excerpt) capped at *char_cap* bytes so the summarizer
    never sees more than this many characters.

    Returns:
        ParsedTranscript dataclass. Always returns — malformed lines are
        skipped, missing fields default to None / 0.

    Refuses:
        Returning more than *char_cap* characters in text_excerpt.
    """
    p = Path(path)
    conv_id: Optional[str] = None
    started: Optional[str] = None
    ended: Optional[str] = None
    cwd: Optional[str] = None
    text_parts: List[str] = []
    text_total = 0
    msg_count = 0
    full_size = 0
    try:
        with p.open("rb") as fh:
            for raw in fh:
                full_size += len(raw)
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except UnicodeDecodeError:
                    continue
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_count += 1
                conv_id = conv_id or obj.get("sessionId") or obj.get("uuid")
                ts = obj.get("timestamp")
                if ts:
                    started = started or ts
                    ended = ts
                cwd = cwd or obj.get("cwd")
                content = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
                # Best-effort text capture from common Claude transcript shapes.
                msg_text = _stringify_message(content) or _stringify_message(obj)
                if msg_text and text_total < char_cap:
                    chunk = msg_text[: max(0, char_cap - text_total)]
                    text_parts.append(chunk)
                    text_total += len(chunk)
    except OSError:
        pass  # HR-8: malformed file → return what we have

    conv_id = conv_id or _conv_id_from_path(p)
    return ParsedTranscript(
        conversation_id=conv_id,
        started_at=started,
        ended_at=ended,
        message_count=msg_count,
        cwd=cwd,
        text_excerpt="\n".join(text_parts),
        full_size=full_size,
    )


def _stringify_message(obj: Any) -> Optional[str]:
    """Pull text out of common Claude message shapes — best effort, never raises."""
    if not isinstance(obj, dict):
        return None
    # Newer schema: message.content is a list of {type, text} blocks
    content = obj.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for blk in content:
            if isinstance(blk, dict) and "text" in blk:
                parts.append(str(blk.get("text") or ""))
        joined = "\n".join(p for p in parts if p)
        if joined:
            return joined
    # Older schema: message.text
    if isinstance(obj.get("text"), str):
        return obj["text"]
    return None


def _conv_id_from_path(p: Path) -> str:
    """Derive a stable conversation id from a transcript filename (fallback only)."""
    stem = p.stem
    if len(stem) >= 8:
        return stem
    return hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class IngestResult:
    """Outcome of a single :func:`ingest_transcript` call."""

    conversation_id: str
    inserted: bool
    skipped_reason: Optional[str]
    used_llm: bool
    tokens_in: int
    tokens_out: int


def ingest_transcript(
    cfg: Config,
    transcript_path: Path,
    *,
    force: bool = False,
    project_hint: Optional[str] = None,
) -> IngestResult:
    """Insert a transcript into ``sessions.db.session_logs`` if guards allow.

    Steps:
        1. Parse the transcript head to learn the conversation id.
        2. Check all guards in order (processed marker, permanent fail,
           cooldown, idle threshold, circuit breaker, attempt cap).
        3. Acquire the global lock; if unavailable within the timeout, skip.
        4. Reserve from the daily token budget if an LLM call is configured.
        5. Optionally call the configured summarizer model.
        6. Insert into ``session_logs``; mark processed; release reservation.
        7. On any failure: bump attempt counter, decrement circuit health,
           clear pending marker. Never raise into the caller.

    Returns:
        IngestResult — never raises. ``skipped_reason`` carries the guard
        that fired when ``inserted`` is False.
    """
    transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        return IngestResult("", False, "transcript-missing", False, 0, 0)

    if not cfg.session_capture_enabled:
        return IngestResult("", False, "capture-disabled", False, 0, 0)

    parsed = parse_transcript(transcript_path, char_cap=cfg.summarizer_char_cap)
    conv_id = parsed.conversation_id

    # Guard 6 — processed marker
    if not force and _is_processed(cfg.install_dir, conv_id):
        return IngestResult(conv_id, False, "already-processed", False, 0, 0)

    # Guard 7 — permanent fail marker
    if not force and _is_permanently_failed(cfg.install_dir, conv_id):
        return IngestResult(conv_id, False, "permanent-failure", False, 0, 0)

    # Guard 2 — cooldown
    if not force and _within_cooldown(
        cfg.install_dir, conv_id, cfg.summarizer_cooldown_sec
    ):
        return IngestResult(conv_id, False, "cooldown", False, 0, 0)

    # Guard 5 — idle threshold (skip active sessions unless forced)
    if not force and not _is_idle(transcript_path, cfg.summarizer_idle_threshold_sec):
        return IngestResult(conv_id, False, "not-idle", False, 0, 0)

    # Guard 9 — circuit breaker
    is_open, secs_left = _circuit_is_open(cfg.install_dir)
    if is_open:
        return IngestResult(
            conv_id, False, f"circuit-open-{secs_left}s", False, 0, 0,
        )

    # Guard 7 — attempt cap (pre-check; we bump after lock)
    if not force and _attempt_count(cfg.install_dir, conv_id) >= cfg.summarizer_attempt_cap:
        _mark_permanent_failure(
            cfg.install_dir, conv_id, reason="attempt-cap-exceeded"
        )
        return IngestResult(conv_id, False, "attempt-cap-exceeded", False, 0, 0)

    # Guard 1 — global lock with timeout
    with _global_lock(cfg.install_dir, cfg.summarizer_lock_timeout_sec) as locked:
        if not locked:
            return IngestResult(conv_id, False, "lock-timeout", False, 0, 0)

        _write_pending(cfg.install_dir, conv_id)
        _bump_attempt(cfg.install_dir, conv_id)
        try:
            used_llm = False
            tokens_in = 0
            tokens_out = 0
            summary_notes = ""

            # Guard 8 — daily token budget (only matters if we'd call an LLM)
            if cfg.summarizer_provider != "off":
                estimated = min(
                    len(parsed.text_excerpt) // 3,  # rough chars→tokens estimate
                    cfg.summarizer_char_cap // 3,
                )
                if check_and_reserve(
                    cfg.install_dir, estimated, cap=cfg.summarizer_daily_token_cap
                ):
                    try:
                        summary_notes, tokens_in, tokens_out = _run_summarizer(
                            cfg, parsed,
                        )
                        used_llm = True
                        record_usage(
                            cfg.install_dir,
                            estimated_tokens=estimated,
                            actual_input=tokens_in,
                            actual_output=tokens_out,
                            cap=cfg.summarizer_daily_token_cap,
                        )
                    except Exception as exc:
                        # Reservation released; mark attempt; do NOT mark processed.
                        record_failure(
                            cfg.install_dir, cap=cfg.summarizer_daily_token_cap,
                        )
                        _circuit_record_failure(cfg.install_dir, cfg)
                        _clear_pending(cfg.install_dir, conv_id)
                        return IngestResult(
                            conv_id, False, f"llm-error:{type(exc).__name__}",
                            False, 0, 0,
                        )

            # Insert metadata row (always — even when LLM is off)
            _insert_session_row(
                cfg, parsed, project_hint=project_hint,
                notes=summary_notes or "metadata-only",
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
            _mark_processed(cfg.install_dir, conv_id)
            _circuit_record_success(cfg.install_dir)
            _clear_pending(cfg.install_dir, conv_id)
            return IngestResult(
                conv_id, True, None, used_llm, tokens_in, tokens_out,
            )
        except Exception as exc:
            _circuit_record_failure(cfg.install_dir, cfg)
            _clear_pending(cfg.install_dir, conv_id)
            return IngestResult(
                conv_id, False, f"insert-error:{type(exc).__name__}",
                False, 0, 0,
            )


def _run_summarizer(
    cfg: Config, parsed: ParsedTranscript,
) -> Tuple[str, int, int]:
    """Call the configured summarizer provider. Network-gated by HR-1.

    Returns:
        (summary_text, tokens_in, tokens_out).

    Raises:
        ValueError: when ``summarizer_provider`` is not a recognized backend.
        RuntimeError: when a backend is configured but its dependency is
            missing or fails. The caller wraps this in attempt/circuit
            accounting and never propagates it into the Stop hook.
    """
    if cfg.summarizer_provider == "off":
        return "", 0, 0
    if cfg.summarizer_provider == "claude-cli":
        return _summarize_via_claude_cli(cfg, parsed)
    raise ValueError(
        f"summarizer_provider={cfg.summarizer_provider!r} is not implemented in "
        "this build. Supported: off | claude-cli. Other providers are gated "
        "behind opt-in entrypoints (HR-1)."
    )


def _summarize_via_claude_cli(
    cfg: Config, parsed: ParsedTranscript,
) -> Tuple[str, int, int]:
    """Shell out to the local ``claude`` CLI (no Python SDK network calls).

    The ``claude`` CLI itself is the network-capable surface; this function
    only spawns a subprocess and reads stdout. It is the only summarizer
    backend that ships in core because it uses the user's existing CLI
    auth — no new credentials needed.

    Refuses:
        Calling claude when the binary is not on PATH (raises RuntimeError).
    """
    import shutil
    import subprocess

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLI not found on PATH")
    prompt = (
        "Summarize this Claude Code session in <=400 words. Sections: "
        "Work Completed (bulleted, with file paths); Decisions; Open Items. "
        "Do not echo transcript content verbatim.\n\n"
        f"---\n{parsed.text_excerpt}"
    )
    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--model", cfg.summarizer_model],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "CLAUDECODE": "", "CLAUDE_CONVERSATION_ID": ""},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"claude CLI failed: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr[:200]}")
    text = result.stdout.strip()
    # The CLI does not report token counts in -p mode; estimate.
    tokens_in = max(1, len(parsed.text_excerpt) // 4)
    tokens_out = max(1, len(text) // 4)
    return text, tokens_in, tokens_out


def _insert_session_row(
    cfg: Config,
    parsed: ParsedTranscript,
    *,
    project_hint: Optional[str],
    notes: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Insert one row into ``sessions.db.session_logs``.

    Refuses:
        Nothing — the call is wrapped in ``INSERT OR IGNORE`` so duplicate
        conversation_ids do not raise.
    """
    sessions_db = cfg.sessions_db or (cfg.install_dir / "sessions.db")
    conn = sqlite3.connect(str(sessions_db))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO session_logs "
            "(conversation_id, tool, machine, project_hint, started_at, "
            "ended_at, full_transcript, token_in, token_out, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                parsed.conversation_id,
                "claude-code",
                os.uname().nodename if hasattr(os, "uname") else "",
                project_hint or _project_hint_from_cwd(parsed.cwd),
                parsed.started_at,
                parsed.ended_at,
                parsed.text_excerpt[: cfg.summarizer_char_cap],
                int(tokens_in),
                int(tokens_out),
                notes[:8000],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _project_hint_from_cwd(cwd: Optional[str]) -> Optional[str]:
    """Derive a project hint from a Claude session's cwd (best effort)."""
    if not cwd:
        return None
    return Path(cwd).name.lower().replace("-", "_") or None


# ---------------------------------------------------------------------------
# Watcher entry point — used by both Stop hook (one transcript) and cron
# ---------------------------------------------------------------------------


def discover_transcripts(
    search_dirs: Optional[List[Path]] = None,
) -> List[Path]:
    """Find Claude Code transcript JSONL files on this machine.

    Default search path: ``~/.claude/projects/*/*.jsonl``.

    Returns:
        List of absolute Paths. Empty when none found.
    """
    if search_dirs is None:
        search_dirs = [Path.home() / ".claude" / "projects"]
    out: List[Path] = []
    for root in search_dirs:
        root = Path(root)
        if not root.exists():
            continue
        out.extend(root.rglob("*.jsonl"))
    return sorted(set(out))


def ingest_all(
    cfg: Config,
    search_dirs: Optional[List[Path]] = None,
    *,
    force: bool = False,
) -> Dict[str, int]:
    """Ingest every transcript on disk that the guards allow.

    Returns:
        Summary dict: ``{seen, inserted, skipped, errors}``. Always returns.
    """
    seen = 0
    inserted = 0
    skipped = 0
    errors = 0
    for path in discover_transcripts(search_dirs):
        seen += 1
        try:
            result = ingest_transcript(cfg, path, force=force)
            if result.inserted:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
    return {
        "seen": seen,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }
