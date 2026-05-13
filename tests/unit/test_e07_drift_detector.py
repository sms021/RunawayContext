"""E7 — predictive drift detector."""
from __future__ import annotations

import pytest

from runaway_context.drift import DriftDetector, run_check

pytestmark = pytest.mark.feature


def test_e07_drift_run_returns_list(seeded_client):
    """E7: run_check returns a list of finding dicts (may be empty)."""
    findings = run_check(seeded_client._knowledge_db, install_dir=seeded_client.install_dir)
    assert isinstance(findings, list)


def test_e07_drift_detector_class_check(seeded_client):
    """E7: DriftDetector.check returns the same shape."""
    detector = DriftDetector(seeded_client._knowledge_db, install_dir=seeded_client.install_dir)
    findings = detector.check()
    assert isinstance(findings, list)
