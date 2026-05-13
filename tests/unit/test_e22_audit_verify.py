"""E22 — audit log + hash chain + verify + tamper detection."""
from __future__ import annotations

import sqlite3

import pytest

from runaway_context import audit
from runaway_context.errors import AuditChainBroken

pytestmark = pytest.mark.feature


def test_e22_append_returns_hash(seeded_client):
    """E22: audit.append returns a stable 32-char hash."""
    conn = sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            h = audit.append(conn, actor="tester", action="test.append")
        assert isinstance(h, str)
        assert len(h) == 32
    finally:
        conn.close()


def test_e22_verify_passes_on_intact_chain(seeded_client):
    """E22: verify returns (True, None, None) on a chain we haven't touched."""
    ok, bad_id, reason = audit.verify(seeded_client._knowledge_db)
    assert ok is True
    assert bad_id is None


def test_e22_tampering_detected(seeded_client):
    """E22: tampering with an audit row is detected by verify."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET action = 'tampered' "
            "WHERE id = (SELECT id FROM audit_log LIMIT 1)"
        )
        conn.commit()
    finally:
        conn.close()
    ok, bad_id, reason = audit.verify(db)
    assert ok is False
    assert bad_id is not None


def test_e22_cli_verify_raises_on_break(seeded_client):
    """E22: cli_verify raises AuditChainBroken on a detected break."""
    db = seeded_client._knowledge_db
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.execute(
            "UPDATE audit_log SET actor = 'rogue' "
            "WHERE id = (SELECT id FROM audit_log LIMIT 1)"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(AuditChainBroken):
        audit.cli_verify(db)
