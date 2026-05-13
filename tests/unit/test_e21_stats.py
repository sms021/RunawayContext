"""E21 — runaway stats dashboard equivalent."""
from __future__ import annotations

import io

import pytest

from runaway_context import stats

pytestmark = pytest.mark.feature


def test_e21_compute_returns_summary_dict(seeded_client):
    """E21: stats.compute returns the documented summary keys."""
    out = stats.compute(seeded_client._knowledge_db,
                        install_dir=seeded_client.install_dir)
    assert "lessons_total" in out
    assert "chunks_total" in out
    assert "lessons_by_maturity" in out
    assert "audit_chain_valid" in out


def test_e21_print_report_writes_text(seeded_client):
    """E21: print_report renders without raising."""
    out = stats.compute(seeded_client._knowledge_db,
                        install_dir=seeded_client.install_dir)
    buf = io.StringIO()
    stats.print_report(out, stream=buf)
    text = buf.getvalue()
    assert text
