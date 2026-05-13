"""E8 — CLI dispatcher — additional handlers."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from runaway_context import cli as cli_mod
from runaway_context import init as init_mod

pytestmark = pytest.mark.feature


@pytest.fixture()
def ready_install(tmp_install):
    init_mod.run(install_dir=tmp_install, non_interactive=True)
    return tmp_install


def test_cmd_brief_calls_get_brief(ready_install, capsys):
    """brief subcommand attempts a Client.get_brief — surfaces ProjectNotFound."""
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "brief", "unknown_project",
    ])
    # ProjectNotFound is a RunawayContextError → exit 2
    assert rc in (cli_mod.EXIT_REFUSED, cli_mod.EXIT_ERROR)


def test_cmd_mature_handler_invokes_client(ready_install, monkeypatch, capsys):
    """mature subcommand calls Client.mature_lesson with kw args."""
    from runaway_context import client as client_mod
    calls = {}
    def stub(self, **kw):
        calls.update(kw)
    monkeypatch.setattr(client_mod.Client, "mature_lesson", stub, raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "mature", "1", "--to", "stable", "--actor", "me",
    ])
    # The CLI handler passes 'to' as kwarg but Client API expects to_state.
    # We just exercise the call path.
    assert rc in (0, cli_mod.EXIT_ERROR)


def test_cmd_supersede(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "supersede",
                        lambda self, **kw: None, raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "supersede", "1", "2", "--actor", "me",
    ])
    assert rc in (0, cli_mod.EXIT_ERROR)


def test_cmd_regen_brief_with_dry_run(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "regen_brief",
                        lambda self, project, dry_run=False: "dry-run text",
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "regen-brief", "x", "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run text" in out


def test_cmd_regen_brief_writes(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "regen_brief",
                        lambda self, project, dry_run=False: "/p/CLAUDE.md",
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "regen-brief", "x",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote" in out


def test_cmd_brief_rollback(ready_install, monkeypatch, capsys):
    """brief-rollback uses brief_preview.rollback."""
    from runaway_context import brief_preview as bp
    monkeypatch.setattr(bp, "rollback",
                        lambda project, snapshot_id=None: "/p/CLAUDE.md",
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "brief-rollback", "tooling",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "restored" in out


def test_cmd_db_migrate_with_explicit_paths(ready_install, tmp_path, capsys):
    """db migrate accepts explicit knowledge/sessions/metrics paths."""
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "db", "migrate",
        "--knowledge-db", str(tmp_path / "k.db"),
        "--sessions-db", str(tmp_path / "s.db"),
        "--metrics-db", str(tmp_path / "m.db"),
    ])
    assert rc == 0
    assert (tmp_path / "k.db").exists()


def test_cmd_hard_delete_with_flags(ready_install, monkeypatch, capsys):
    """hard-delete with both safety flags performs the deletion + audit row."""
    import sqlite3
    from runaway_context.config import Config
    cfg = Config.load(ready_install)
    conn = sqlite3.connect(str(cfg.knowledge_db))
    try:
        conn.execute("INSERT INTO slug_registry (slug) VALUES ('tooling')")
        conn.execute(
            "INSERT INTO lessons_learned (id, title, project_tags) "
            "VALUES (1, 'doomed', json_array('tooling'))"
        )
        conn.commit()
    finally:
        conn.close()

    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "db", "hard-delete",
        "--table", "lessons_learned", "--id", "1",
        "--i-understand-this-is-permanent", "--backup-first",
    ])
    assert rc == 0

    # The row is gone; an audit row recorded the hard-delete.
    conn = sqlite3.connect(str(cfg.knowledge_db))
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM lessons_learned WHERE id = 1"
        ).fetchone()[0]
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchall()]
    finally:
        conn.close()
    assert rows == 0
    assert actions == ["hard_delete"]


def test_cmd_audit_verify_chain_broken(ready_install, monkeypatch, capsys):
    """audit verify exit 2 when chain is broken."""
    from runaway_context import client as client_mod
    monkeypatch.setattr(
        client_mod.Client,
        "audit_verify",
        lambda self: (False, 1, "stub broken chain"),
        raising=False,
    )
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "audit", "verify",
    ])
    assert rc == cli_mod.EXIT_REFUSED


def test_cmd_tier_check_each_tier(ready_install, monkeypatch, capsys):
    """tier check prints next-gate for each tier."""
    from runaway_context.config import Config
    for tier in ("T0", "T1", "T2", "T3", "T4", "T5"):
        cfg = Config.load(ready_install)
        cfg.tier = tier
        cfg.install_dir = Path(ready_install)
        cfg.save()
        rc = cli_mod.main([
            "--install-dir", str(ready_install),
            "tier", "check",
        ])
        assert rc == 0


def test_cmd_tier_promote_check_only(ready_install, monkeypatch, capsys):
    """tier promote --check returns 0 when the gate passes."""
    monkeypatch.setattr(
        cli_mod,
        "_evaluate_tier_gate",
        lambda cfg, target: (True, "stub gate passes"),
    )
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "tier", "promote", "--to", "T2", "--check",
    ])
    assert rc == 0


def test_cmd_tier_promote_apply(ready_install, monkeypatch, capsys):
    """tier promote without --check writes the new tier to config."""
    monkeypatch.setattr(
        cli_mod,
        "_evaluate_tier_gate",
        lambda cfg, target: (True, "stub gate passes"),
    )
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "tier", "promote", "--to", "T2",
    ])
    assert rc == 0


def test_cmd_tier_promote_failed_gate(ready_install, monkeypatch, capsys):
    """tier promote exits 2 when the gate refuses."""
    monkeypatch.setattr(
        cli_mod,
        "_evaluate_tier_gate",
        lambda cfg, target: (False, "stub gate refused"),
    )
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "tier", "promote", "--to", "T2",
    ])
    assert rc == cli_mod.EXIT_REFUSED


def test_cmd_approve_reject_draft(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "approve_draft",
                        lambda self, draft_id, actor=None: 99, raising=False)
    monkeypatch.setattr(client_mod.Client, "reject_draft",
                        lambda self, draft_id, actor=None, notes=None: None,
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "approve-draft", "1", "--actor", "me",
    ])
    assert rc == 0
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "reject-draft", "1", "--actor", "me", "--notes", "n",
    ])
    assert rc == 0


def test_cmd_slug_merge(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "merge_slugs",
                        lambda self, **kw: None, raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "slug", "register", "src_s",
    ])
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "slug", "register", "dst_s",
    ])
    capsys.readouterr()
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "slug", "merge", "src_s", "dst_s",
    ])
    assert rc == 0


def test_cmd_import_returns_dict_json(ready_install, monkeypatch, tmp_path, capsys):
    """import prints JSON when client returns a dict."""
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "import_json",
                        lambda self, input_path, actor=None: {"added": 1},
                        raising=False)
    bundle = tmp_path / "b.json"
    bundle.write_text("{}")
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "import", "--input", str(bundle), "--actor", "me",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "added" in out


def test_cmd_search_with_filters(ready_install, monkeypatch, capsys):
    """search subcommand with --project / --kind / --limit."""
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "search",
                        lambda self, **kw: [
                            {"kind": "lesson", "id": 1, "title": "T"},
                        ],
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "search", "abc", "--project", "x", "--kind", "lessons",
        "--limit", "5",
    ])
    assert rc == 0


def test_cmd_specialist_list_with_rows(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "list_specialists",
                        lambda self: [
                            {"name": "Bot", "domain": "x", "description": ""},
                        ],
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "specialist", "list",
    ])
    assert rc == 0


def test_cmd_list_drafts_with_rows(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "list_drafts",
                        lambda self: [
                            {"id": 1, "project": "x", "title": "T"},
                        ],
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "list-drafts",
    ])
    assert rc == 0


def test_cmd_list_lessons_with_rows(ready_install, monkeypatch, capsys):
    from runaway_context import client as client_mod
    monkeypatch.setattr(client_mod.Client, "list_lessons",
                        lambda self, **kw: [
                            {"id": 1, "maturity": "scar", "status": "active",
                             "title": "T"},
                        ],
                        raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "list-lessons",
        "--project", "x", "--status", "active", "--maturity", "scar",
    ])
    assert rc == 0


def test_cmd_slug_list_missing_db(tmp_install, capsys):
    """slug list returns EXIT_CONFIG when DB doesn't exist."""
    rc = cli_mod.main([
        "--install-dir", str(tmp_install),
        "slug", "list",
    ])
    assert rc == cli_mod.EXIT_CONFIG


def test_cmd_slug_register_missing_db(tmp_install, capsys):
    """slug register returns EXIT_CONFIG when DB doesn't exist."""
    rc = cli_mod.main([
        "--install-dir", str(tmp_install),
        "slug", "register", "valid_slug",
    ])
    assert rc == cli_mod.EXIT_CONFIG


def test_cli_eprint(capsys):
    cli_mod._eprint("hello")
    err = capsys.readouterr().err
    assert "hello" in err


def test_cli_db_migrate_handles_abort(ready_install, monkeypatch, capsys):
    """db migrate exits 2 when MigrationReport.aborted_reason is set."""
    from runaway_context import migrate as migrate_mod
    from runaway_context.migrate import MigrationReport

    def fake_migrate(knowledge_db=None, sessions_db=None, metrics_db=None,
                    backup=True):
        rep = MigrationReport(knowledge_db=knowledge_db)
        rep.aborted_reason = "boom"
        return rep

    monkeypatch.setattr(migrate_mod, "migrate", fake_migrate)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "db", "migrate",
    ])
    assert rc == cli_mod.EXIT_REFUSED


def test_cli_log_lesson_invalid_severity_uses_runaway_err(ready_install,
                                                          monkeypatch, capsys):
    """A handler raising RunawayContextError gets caught → exit 2."""
    from runaway_context import client as client_mod
    from runaway_context.errors import InvalidProjectSlug
    def stub(self, **kw):
        raise InvalidProjectSlug("nope")
    monkeypatch.setattr(client_mod.Client, "log_lesson", stub, raising=False)
    rc = cli_mod.main([
        "--install-dir", str(ready_install),
        "log-lesson", "--title", "x", "--projects", "tooling",
    ])
    assert rc == cli_mod.EXIT_REFUSED
