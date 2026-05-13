"""E20 — brief preview/snapshot/rollback — extra coverage."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runaway_context import brief_preview

pytestmark = pytest.mark.feature


def _wire_card(client, project="tooling"):
    md_path = client.install_dir / "briefs" / project / "CLAUDE.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# tooling\nseed content\n")
    conn = sqlite3.connect(str(client._knowledge_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_context_card "
            "(project, active_lesson_ids, active_chunk_ids, top_warnings, md_path, md_line_cap) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project, "[]", "[]", "[]", str(md_path), 200),
        )
        conn.commit()
    finally:
        conn.close()
    return md_path


def test_brief_preview_uses_regenerate(seeded_client):
    """preview returns regenerator output and the project key is added."""
    _wire_card(seeded_client)
    out = brief_preview.preview(seeded_client.install_dir, "tooling")
    assert out["project"] == "tooling"


def test_snapshot_missing_file_raises(seeded_client):
    """snapshot raises FileNotFoundError when md is missing."""
    with pytest.raises(FileNotFoundError):
        brief_preview.snapshot(seeded_client.install_dir, "no_such_project")


def test_list_snapshots_invalid_limit(seeded_client):
    with pytest.raises(ValueError):
        brief_preview.list_snapshots(seeded_client.install_dir, "tooling", limit=0)


def test_get_snapshot_returns_none_when_missing(seeded_client):
    """get_snapshot returns None on unknown id."""
    out = brief_preview.get_snapshot(seeded_client.install_dir, 99999)
    assert out is None


def test_get_snapshot_returns_dict(seeded_client):
    """get_snapshot returns full content dict."""
    _wire_card(seeded_client)
    sid = brief_preview.snapshot(seeded_client.install_dir, "tooling",
                                 note="n", saved_by="me")
    out = brief_preview.get_snapshot(seeded_client.install_dir, sid)
    assert out is not None
    assert out["note"] == "n"


def test_get_snapshot_requires_int(seeded_client):
    with pytest.raises(TypeError):
        brief_preview.get_snapshot(seeded_client.install_dir, "not int")


def test_rollback_requires_actor(seeded_client):
    """rollback rejects empty actor."""
    _wire_card(seeded_client)
    brief_preview.snapshot(seeded_client.install_dir, "tooling")
    with pytest.raises(ValueError):
        brief_preview.rollback(seeded_client.install_dir, "tooling",
                               actor="")


def test_rollback_no_snapshot_raises(seeded_client):
    """rollback with no snapshot for project raises LookupError."""
    with pytest.raises(LookupError):
        brief_preview.rollback(seeded_client.install_dir,
                               "never_snapshotted", actor="me")


def test_rollback_to_latest(seeded_client):
    """rollback with snapshot_id=None restores the most recent snapshot."""
    md_path = _wire_card(seeded_client)
    brief_preview.snapshot(seeded_client.install_dir, "tooling")
    md_path.write_text("overwritten\n")
    out = brief_preview.rollback(seeded_client.install_dir, "tooling",
                                 actor="me")
    assert out["actor"] == "me"
    assert md_path.read_text() == "# tooling\nseed content\n"


def test_rollback_wrong_project_raises(seeded_client):
    """rollback with snapshot_id whose project differs raises ValueError."""
    _wire_card(seeded_client)
    sid = brief_preview.snapshot(seeded_client.install_dir, "tooling")
    seeded_client.register_slug("other")
    with pytest.raises(ValueError):
        brief_preview.rollback(seeded_client.install_dir, "other",
                               snapshot_id=sid, actor="me")


def test_preview_brief_not_importable(seeded_client, monkeypatch):
    """preview falls back when runaway_context.brief import fails."""
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    md_path = _wire_card(seeded_client)
    out = brief_preview.preview(seeded_client.install_dir, "tooling")
    assert out["regenerator_available"] is False
    assert "content" in out


def test_preview_brief_returns_string(seeded_client, monkeypatch):
    """preview wraps a string-returning regenerator into the canonical shape."""
    import importlib
    real_import = importlib.import_module

    class Stub:
        @staticmethod
        def regenerate(install_dir=None, project=None, dry_run=False):
            return "raw markdown content"

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            return Stub
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = brief_preview.preview(seeded_client.install_dir, "tooling")
    assert out["content"] == "raw markdown content"
    assert out["regenerator_available"] is True


def test_preview_brief_no_regenerate_attr(seeded_client, monkeypatch):
    """preview handles brief module without regenerate function."""
    import importlib
    real_import = importlib.import_module

    class Empty:
        pass

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            return Empty
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = brief_preview.preview(seeded_client.install_dir, "tooling")
    assert out["regenerator_available"] is False


def test_preview_brief_unexpected_return_type(seeded_client, monkeypatch):
    """preview raises TypeError when regenerate returns something weird."""
    import importlib
    real_import = importlib.import_module

    class Stub:
        @staticmethod
        def regenerate(install_dir=None, project=None, dry_run=False):
            return 42  # not str or dict

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            return Stub
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(TypeError):
        brief_preview.preview(seeded_client.install_dir, "tooling")


def test_record_audit_inline_fallback(seeded_client, monkeypatch):
    """_record_audit falls back to inline insert when audit module is missing."""
    import importlib
    import sqlite3 as _sqlite3
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "runaway_context.audit":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    conn = _sqlite3.connect(str(seeded_client._knowledge_db))
    conn.row_factory = _sqlite3.Row
    try:
        before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        brief_preview._record_audit(
            conn, actor="me", action="brief_rollback",
            target_id=1, details={"x": 1},
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after > before
    finally:
        conn.close()


def test_resolve_md_path_via_brief_module_helper(seeded_client, monkeypatch):
    """_resolve_md_path uses brief.md_path_for when available."""
    import importlib
    real_import = importlib.import_module

    class Stub:
        @staticmethod
        def md_path_for(install_dir, project):
            return install_dir / "custom" / f"{project}.md"

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            return Stub
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = brief_preview._resolve_md_path(seeded_client.install_dir, "tooling")
    assert out == seeded_client.install_dir / "custom" / "tooling.md"


def test_resolve_md_path_brief_import_error(seeded_client, monkeypatch):
    """_resolve_md_path falls back when brief is not importable."""
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "runaway_context.brief":
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = brief_preview._resolve_md_path(seeded_client.install_dir, "tooling")
    assert out == seeded_client.install_dir / "briefs" / "tooling" / "CLAUDE.md"
