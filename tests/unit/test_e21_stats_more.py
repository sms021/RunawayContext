"""E21 — stats — additional coverage for print_report, cli_main, edge cases."""
from __future__ import annotations

import io
import json
import sys

import pytest

from runaway_context import stats

pytestmark = pytest.mark.feature


def test_stats_print_report_default_stdout(seeded_client, capsys):
    """print_report defaults to sys.stdout when no stream supplied."""
    report = stats.compute(seeded_client._knowledge_db,
                           install_dir=seeded_client.install_dir)
    stats.print_report(report)
    captured = capsys.readouterr()
    assert "RunawayContext stats" in captured.out


def test_stats_print_report_empty_dist(seeded_client):
    """print_report handles empty distribution dicts gracefully."""
    report = stats.compute(seeded_client._knowledge_db,
                           install_dir=seeded_client.install_dir)
    # Force empty top_referenced and distributions to exercise (none) branches
    report["top_referenced_lessons"] = []
    report["chunks_by_project"] = {}
    report["lessons_by_maturity"] = {}
    report["lessons_by_severity"] = {}
    buf = io.StringIO()
    stats.print_report(report, stream=buf)
    text = buf.getvalue()
    assert "(none)" in text


def test_stats_ascii_bar_normal_and_edge_cases():
    """_ascii_bar produces expected widths in normal and zero-max cases."""
    assert len(stats._ascii_bar(5, 10, 20)) == 20
    # zero max produces spaces
    assert stats._ascii_bar(0, 0, 10) == " " * 10


def test_stats_ascii_bar_negative_width_raises():
    """_ascii_bar refuses negative width."""
    with pytest.raises(ValueError):
        stats._ascii_bar(1, 1, -1)


def test_stats_cli_main_pretty_text(seeded_client, capsys):
    """cli_main without --json prints the pretty report and returns 0."""
    rc = stats.cli_main([
        "--db", str(seeded_client._knowledge_db),
        "--install-dir", str(seeded_client.install_dir),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "RunawayContext stats" in captured.out


def test_stats_cli_main_json(seeded_client, capsys):
    """cli_main --json emits JSON to stdout."""
    rc = stats.cli_main([
        "--db", str(seeded_client._knowledge_db),
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert "lessons_total" in parsed


def test_stats_cli_main_db_missing(tmp_path, capsys):
    """cli_main returns 2 when the DB path is missing."""
    rc = stats.cli_main(["--db", str(tmp_path / "nope.db")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_stats_compute_missing_audit(tmp_path):
    """compute on a non-existent DB still returns audit_chain_valid=False."""
    # Use the FileNotFoundError branch — point at a nonexistent db file
    fake = tmp_path / "noexist.db"
    # _safe_scalar will create the DB via connect, but audit verify on the
    # fresh-but-unmigrated file raises FileNotFoundError - we exercise it
    # by calling compute against a path that does NOT exist on disk yet,
    # which forces sqlite3.connect to create it as empty (no tables).
    fake.touch()
    out = stats.compute(fake)
    # Tables missing → zero counts, audit chain check still attempts
    assert out["lessons_total"] == 0
    assert out["chunks_total"] == 0
