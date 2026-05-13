"""E1 — migrator — extra coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from runaway_context.migrate import MigrationReport, migrate, schema_version

pytestmark = pytest.mark.feature


def test_migration_report_succeeded_flag():
    rep = MigrationReport(knowledge_db=Path("/tmp/x"))
    assert rep.succeeded is True
    rep.aborted_reason = "X"
    assert rep.succeeded is False


def test_migrate_creates_backup(tmp_install):
    """Migrate against an existing file → backup created."""
    db = tmp_install / "k.db"
    # First migrate to create the file
    migrate(db)
    assert db.exists()
    # Second migrate — file exists, should backup
    rep = migrate(db)
    assert rep.backup_path is not None
    assert Path(rep.backup_path).exists()


def test_migrate_no_backup(tmp_install):
    """backup=False skips making the backup."""
    db = tmp_install / "k.db"
    migrate(db)
    rep = migrate(db, backup=False)
    assert rep.backup_path is None


def test_migrate_creates_sessions_and_metrics(tmp_install):
    """sessions_db + metrics_db paths get migrated independently."""
    rep = migrate(
        knowledge_db=tmp_install / "k.db",
        sessions_db=tmp_install / "s.db",
        metrics_db=tmp_install / "m.db",
    )
    assert (tmp_install / "s.db").exists()
    assert (tmp_install / "m.db").exists()
    assert any("sessions:" in s for s in rep.steps_applied)
    assert any("metrics:" in s for s in rep.steps_applied)


def test_schema_version_missing_db(tmp_path):
    """schema_version returns None for non-existent DB."""
    assert schema_version(tmp_path / "nope.db") is None


def test_schema_version_after_migrate(tmp_install):
    db = tmp_install / "k.db"
    migrate(db)
    ver = schema_version(db)
    assert ver is not None
    assert ver[0] == 3
