"""E8 — environment doctor + tier recommender tests.

The doctor (``runaway doctor``) is the diagnostic engine adopters' AIs walk
after install. The tier recommender drives the wizard's ladder-selection.
Both are read-only — these tests verify their behavior end-to-end on a
freshly-migrated install.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runaway_context import doctor
from runaway_context.cli import main as cli_main
from runaway_context.config import Config
from runaway_context.init import recommend_tier
from runaway_context.migrate import migrate


pytestmark = pytest.mark.feature


# ---------------------------------------------------------------------------
# Tier recommender
# ---------------------------------------------------------------------------


def test_recommend_t0_when_no_lessons_yet():
    """T0 should never be picked just from headcount — the wizard handles that."""
    # headcount=1 → at minimum T1 (T0 is only when the user explicitly
    # picks "I haven't logged anything yet" path in the wizard, which we
    # express by overriding the recommendation interactively).
    assert recommend_tier(headcount=1) == "T1"


def test_recommend_t1_solo_single_project():
    """A solo developer on one project belongs at T1."""
    assert recommend_tier(headcount=1, multi_project=False) == "T1"


def test_recommend_t2_solo_multi_project():
    """A solo developer juggling projects belongs at T2."""
    assert recommend_tier(headcount=1, multi_project=True) == "T2"


def test_recommend_t3_small_team():
    """Two to five collaborators land at T3."""
    for n in (2, 3, 4, 5):
        assert recommend_tier(headcount=n) == "T3"


def test_recommend_t4_team_with_review():
    """5+ with review process lands at T4."""
    assert recommend_tier(headcount=8, is_team_with_review_process=True) == "T4"


def test_recommend_t5_org_with_sso():
    """20+ with SSO lands at T5."""
    assert (
        recommend_tier(headcount=25, is_team_with_review_process=True, has_sso=True)
        == "T5"
    )


def test_recommend_coerces_bad_headcount():
    """Zero / negative headcount is coerced to 1 (single user)."""
    assert recommend_tier(headcount=0) == "T1"
    assert recommend_tier(headcount=-3) == "T1"


# ---------------------------------------------------------------------------
# Doctor — clean migrated install
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_install(tmp_path):
    """Provide a freshly-migrated install for doctor tests."""
    migrate(
        tmp_path / "knowledge.db",
        tmp_path / "sessions.db",
        tmp_path / "metrics.db",
        backup=False,
    )
    return tmp_path


def test_doctor_runs_on_fresh_install(fresh_install):
    """All checks return findings; no FAIL on a clean migrated install."""
    findings = doctor.run_diagnostics(install_dir=fresh_install)
    assert findings, "doctor produced no findings"
    fails = [f for f in findings if f.level == "fail"]
    assert fails == [], f"unexpected FAILs: {[f.code for f in fails]}"


def test_doctor_warns_on_missing_slugs(fresh_install):
    """Empty slug_registry should yield a WARN for HR-2 prerequisites."""
    findings = doctor.run_diagnostics(install_dir=fresh_install)
    by_code = {f.code: f for f in findings}
    assert "SLUG_REGISTRY" in by_code
    assert by_code["SLUG_REGISTRY"].level == "warn"
    assert "runaway slug register" in by_code["SLUG_REGISTRY"].remediation


def test_doctor_fail_on_empty_install(tmp_path):
    """Doctor should not crash on an install dir with no DB; INSTALL_DIR ok, SCHEMA absent."""
    cfg = Config.load(tmp_path)
    cfg.save()
    findings = doctor.run_diagnostics(install_dir=tmp_path)
    # No knowledge.db means schema-dependent checks are skipped.
    codes = {f.code for f in findings}
    assert "INSTALL_DIR" in codes
    assert "SCHEMA_VERSION" not in codes  # skipped when DB absent


def test_doctor_json_output_parses(fresh_install):
    """``runaway doctor --json`` emits a parseable findings array."""
    findings = doctor.run_diagnostics(install_dir=fresh_install)
    out = doctor.render_json(findings)
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == len(findings)
    for f in parsed:
        assert "level" in f and "code" in f and "message" in f


def test_doctor_text_report_groups_by_level(fresh_install):
    """The text report includes section headers for FAIL/WARN/OK."""
    findings = doctor.run_diagnostics(install_dir=fresh_install)
    text = doctor.render_report(findings)
    assert "runaway doctor" in text
    assert "summary:" in text
    # OK section is always present after a clean migrate
    assert "OK:" in text


def test_doctor_cli_main_returns_zero_when_no_failures(fresh_install, capsys):
    """``doctor.cli_main`` exits 0 when no FAIL findings exist."""
    rc = doctor.cli_main(install_dir=fresh_install)
    assert rc == 0


def test_doctor_via_cli(fresh_install, capsys):
    """``runaway doctor`` dispatches through the CLI cleanly."""
    rc = cli_main(["--install-dir", str(fresh_install), "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "runaway doctor" in out


def test_doctor_via_cli_json(fresh_install, capsys):
    """``runaway doctor --json`` emits parseable JSON."""
    rc = cli_main(["--install-dir", str(fresh_install), "doctor", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert any(f["code"] == "PY_VERSION" for f in parsed)


def test_doctor_check_python_version_passes():
    """Python 3.8+ should pass PY_VERSION."""
    f = doctor.check_python_version()
    assert f.level == "ok"
    assert f.code == "PY_VERSION"


def test_doctor_check_fts5_available():
    """FTS5 should be available in the running Python's sqlite."""
    f = doctor.check_fts5_available()
    assert f.level == "ok"


def test_doctor_check_optional_module_missing():
    """Asking for a clearly-missing module yields a WARN with remediation."""
    f = doctor.check_optional_module(
        "totally_not_a_real_module_xyz",
        description="never installed",
    )
    assert f.level == "warn"
    assert "pip install" in f.remediation


def test_doctor_check_tier_gate_returns_next(fresh_install):
    """check_tier_gate reports the current tier and the next gate text."""
    cfg = Config.load(fresh_install)
    cfg.tier = "T1"
    cfg.save()
    f = doctor.check_tier_gate(cfg)
    assert f.level == "ok"
    assert "T1" in f.message
    assert f.extra.get("tier") == "T1"
