"""Contract tests for Part VI.4 — runtime self-checks at Client startup.

The plan requires the Client to refuse to start if:

* HR-1: A network-capable module is loaded without its config flag.
* HR-4: The schema version doesn't match the code's expected major version.
* HR-7: Audit log chain verification fails.
* HR-9: ``lessons_learned.maturity`` has values outside the canonical enum.

HR-1 and HR-4 are exercised by the existing HR-1 and HR-4 tests. This module
exercises the two newer self-checks (HR-7 and HR-9) so that *every* Part VI.4
clause has a named test.
"""

from __future__ import annotations

import sqlite3

import pytest

from runaway_context import Client
from runaway_context.errors import AuditChainBroken, MaturityEnumViolation
from runaway_context.migrate import migrate


pytestmark = pytest.mark.contract


def _fresh_install(tmp_path):
    """Build a freshly-migrated install directory and return its path."""
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    return tmp_path


def test_runtime_check_refuses_invalid_maturity(tmp_path):
    """HR-9: external SQL that wrote a bad maturity must refuse Client startup.

    Refuses:
        Operating on a DB whose maturity column was hand-edited around the
        trigger that normally enforces the enum.
    """
    install = _fresh_install(tmp_path)
    Client(install_dir=install)  # initial startup succeeds

    # Insert a real lesson then poison the maturity column with external SQL.
    # We drop the trigger so the bypass is possible — this simulates a sysadmin
    # who edited the DB by hand.
    conn = sqlite3.connect(str(install / "knowledge.db"))
    try:
        conn.execute(
            "INSERT INTO lessons_learned (title, project_tags, maturity) "
            "VALUES ('seed', json_array('tooling'), 'active')"
        )
        conn.execute("DROP TRIGGER ll_maturity_valid_state")
        conn.execute("UPDATE lessons_learned SET maturity = 'garbage'")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(MaturityEnumViolation):
        Client(install_dir=install)


def test_runtime_check_refuses_broken_audit_chain(tmp_path):
    """HR-7: a tampered audit_log must refuse Client startup.

    Refuses:
        Operating on a DB whose audit_log chain does not verify.
    """
    install = _fresh_install(tmp_path)

    conn = sqlite3.connect(str(install / "knowledge.db"))
    try:
        # Two rows whose hashes don't actually chain — first row's this_hash
        # is a literal, second's previous_hash is unrelated.
        conn.execute(
            "INSERT INTO audit_log (actor, action, previous_hash, this_hash) "
            "VALUES ('a', 'test', NULL, 'aaaa')"
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, previous_hash, this_hash) "
            "VALUES ('a', 'test', 'bbbb', 'cccc')"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AuditChainBroken):
        Client(install_dir=install)


def test_runtime_check_starts_clean_install(tmp_path):
    """Sanity: a freshly-migrated install passes all four runtime self-checks."""
    install = _fresh_install(tmp_path)
    Client(install_dir=install)  # must not raise
