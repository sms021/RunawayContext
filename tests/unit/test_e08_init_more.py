"""E8 — init wizard — interactive branch coverage."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from runaway_context import init as init_mod
from runaway_context.config import Config

pytestmark = pytest.mark.feature


def _input_seq(values):
    """Return a callable that returns the next value from `values` each call."""
    iterator = iter(values)

    def _ask(prompt=""):
        try:
            return next(iterator)
        except StopIteration:
            return ""

    return _ask


def test_init_interactive_full(tmp_install, monkeypatch, capsys):
    """Drive the interactive wizard end-to-end with scripted input.

    The wizard's tier step now walks the recommender first (headcount,
    multi-project) and then offers an override slot — this test feeds a
    solo, multi-project answer set so the recommendation lands at T2.
    """
    answers = [
        # No existing install → upgrade prompt SKIPPED.
        str(tmp_install),
        # tier recommender — headcount:
        "1",
        # multi_project? -> y  (so recommendation = T2)
        "y",
        # accept the recommendation
        "",
        # mcp_enabled? -> n
        "n",
        # telemetry? -> y
        "y",
        # templates: copy_them prompt — answer 'n'
        "n",
        # slug input
        "tooling, oTHER , bad slug!",
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=False)
    assert cfg.tier == "T2"
    assert cfg.mcp_enabled is False
    assert cfg.telemetry_enabled is True
    assert (tmp_install / "knowledge.db").exists()


def test_init_interactive_invalid_tier_falls_back(tmp_install, monkeypatch):
    """Unknown tier override falls back to the recommendation."""
    answers = [
        str(tmp_install),
        "1",       # headcount
        "n",       # multi_project? -> no, recommendation = T1
        "T99",     # override attempt — invalid → falls back to T1
        "n",       # mcp
        "n",       # telemetry
        "n",       # copy templates
        "general", # slug
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=False)
    assert cfg.tier == "T1"


def test_init_interactive_upgrade_prompt(tmp_install, monkeypatch):
    """When existing config exists, wizard asks about upgrade."""
    # First do non-interactive init to create config.json
    init_mod.run(install_dir=tmp_install, non_interactive=True)
    answers = [
        "y",                # upgrade in place
        str(tmp_install),   # dir
        "1",                # headcount
        "n",                # multi_project? -> no
        "",                 # accept recommendation T1
        "n",                # mcp
        "y",                # telemetry
        "n",                # copy templates
        "tooling",          # slug
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=False)
    assert cfg.tier == "T1"


def test_ask_yes_no_handles_ambiguous(monkeypatch):
    """_ask_yes_no re-prompts until a y/n is given."""
    answers = iter(["maybe", "huh", "yes"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert init_mod._ask_yes_no("?")


def test_ask_yes_no_eof_default(monkeypatch):
    """EOF returns the default."""
    def _eof(*_):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    # default y
    assert init_mod._ask_yes_no("?", default=True)
    # default n
    assert init_mod._ask_yes_no("?", default=False) is False


def test_register_slugs_invalid_skipped(tmp_install, capsys):
    """_register_slugs skips slugs that fail format validation."""
    init_mod.run(install_dir=tmp_install, non_interactive=True)
    inserted = init_mod._register_slugs(tmp_install,
                                        ["validslug", "Bad Slug!"])
    assert "validslug" in inserted
    assert "Bad Slug!" not in inserted


def test_register_slugs_no_db(tmp_path):
    """_register_slugs returns [] when knowledge.db doesn't exist."""
    out = init_mod._register_slugs(tmp_path / "no_install", ["x"])
    assert out == []


def test_templates_root_returns_path():
    """_templates_root returns a Path regardless of whether dir exists."""
    out = init_mod._templates_root()
    assert isinstance(out, Path)


def test_list_template_dirs_missing(tmp_path):
    """_list_template_dirs returns [] when root doesn't exist."""
    assert init_mod._list_template_dirs(tmp_path / "no_such") == []


def test_list_template_dirs_with_subdirs(tmp_path):
    """_list_template_dirs lists subdirectories."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c_file.txt").write_text("file")
    out = init_mod._list_template_dirs(tmp_path)
    assert [p.name for p in out] == ["a", "b"]


def test_init_interactive_with_templates(tmp_install, monkeypatch, tmp_path):
    """When templates exist, picking one copies it under install_dir/templates/."""
    # Monkey-patch _templates_root to point at a tmp template dir we control
    tpl_root = tmp_path / "templates"
    tpl_root.mkdir()
    tpl_a = tpl_root / "alpha"
    tpl_a.mkdir()
    (tpl_a / "file1.txt").write_text("hello")
    monkeypatch.setattr(init_mod, "_templates_root", lambda: tpl_root)

    answers = [
        str(tmp_install),  # dir
        "1",               # headcount
        "n",               # multi_project? -> no, recommendation = T1
        "",                # accept recommendation
        "n",               # mcp
        "y",               # telemetry
        "y",               # copy templates
        "1",               # pick first template
        "tooling",         # slug
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    init_mod.run(install_dir=tmp_install, non_interactive=False)
    copied = tmp_install / "templates" / "alpha" / "file1.txt"
    assert copied.exists()


def test_init_interactive_template_out_of_range(tmp_install, monkeypatch, tmp_path):
    """Out-of-range template index → friendly skip without crashing."""
    tpl_root = tmp_path / "tpls"
    tpl_root.mkdir()
    (tpl_root / "only").mkdir()
    monkeypatch.setattr(init_mod, "_templates_root", lambda: tpl_root)

    answers = [
        str(tmp_install),
        "1", "n", "",   # tier recommender → T1
        "n", "y",       # mcp / telemetry
        "y",            # copy templates yes
        "99",           # out of range
        "general",
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=False)
    assert cfg is not None


def test_init_interactive_template_skip_blank(tmp_install, monkeypatch, tmp_path):
    """Blank template pick → skip without crashing."""
    tpl_root = tmp_path / "tpls"
    tpl_root.mkdir()
    (tpl_root / "only").mkdir()
    monkeypatch.setattr(init_mod, "_templates_root", lambda: tpl_root)

    answers = [
        str(tmp_install),
        "T1", "n", "y",
        "y",  # copy templates yes
        "",  # blank skip
        "general",
    ]
    monkeypatch.setattr("builtins.input", _input_seq(answers))
    cfg = init_mod.run(install_dir=tmp_install, non_interactive=False)
    assert cfg is not None
