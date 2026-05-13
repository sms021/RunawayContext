"""HR-7 contract tests — audit_log is append-only and chain-verifiable.

HR-7: every write that mutates the knowledge store appends a row to
``audit_log``. UPDATEs and DELETEs on audit_log are blocked by SQL triggers.
``audit.verify`` walks the chain and surfaces tampering.
"""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context import audit

pytestmark = pytest.mark.contract


def test_hr_07_chain_verifies_after_appends(seeded_client) -> None:
    """HR-7: post-seed audit chain verifies as intact."""
    ok, bad_id, reason = audit.verify(seeded_client._knowledge_db)
    assert ok, f"chain broken at {bad_id}: {reason}"


def test_hr_07_update_audit_log_rejected(seeded_client) -> None:
    """HR-7: UPDATE on audit_log is refused by SQL trigger."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        row = conn.execute("SELECT id FROM audit_log LIMIT 1").fetchone()
        assert row is not None
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE audit_log SET actor = 'tamper' WHERE id = ?",
                (row[0],),
            )
            conn.commit()
    finally:
        conn.close()


def test_hr_07_delete_audit_log_rejected(seeded_client) -> None:
    """HR-7: DELETE on audit_log is refused by SQL trigger."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE id IN (SELECT id FROM audit_log LIMIT 1)")
            conn.commit()
    finally:
        conn.close()


def test_hr_07_tampering_detected_by_verify(seeded_client) -> None:
    """HR-7: a hash-chain break is detected by audit.verify.

    The DB triggers block UPDATE/DELETE, so we simulate tampering by
    dropping the triggers and rewriting an audit_log row — this models
    an adversary with write access. After tampering, ``verify`` returns
    (False, bad_id, reason).
    """
    db_path = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db_path))
    try:
        # Bypass the append-only triggers temporarily so we can simulate
        # an external mutation event.
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
        conn.execute(
            "UPDATE audit_log SET actor = 'tampered-actor' "
            "WHERE id = (SELECT id FROM audit_log LIMIT 1)"
        )
        conn.commit()
    finally:
        conn.close()

    ok, bad_id, reason = audit.verify(db_path)
    assert ok is False
    assert bad_id is not None
    assert reason
