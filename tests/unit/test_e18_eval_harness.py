"""E18 — evaluation harness + 10 standard tasks + scrub pipeline."""
from __future__ import annotations

import pytest

from runaway_context.eval.harness import (
    EvalHarness, mrr, ndcg_at_k, precision_at_k, pretty_print, recall_at_k,
)
from runaway_context.eval.scrub import EMAIL_RE, HOST_RE, IP_RE, scrub_record, scrub_text
from runaway_context.eval.tasks import STANDARD_TASKS, load_tasks

pytestmark = pytest.mark.feature


def test_e18_standard_tasks_count():
    """E18: STANDARD_TASKS contains 10 tasks."""
    assert len(STANDARD_TASKS) == 10


def test_e18_load_tasks_default():
    """E18: load_tasks() returns the standard suite when path is None."""
    out = load_tasks(None)
    assert len(out) == 10


def test_e18_precision_recall_metrics_basic():
    """E18: per-task metric primitives produce floats in [0, 1]."""
    results = [{"title": "AFP → Pay App terminology"}, {"title": "other"}]
    gold = ["AFP → Pay App terminology"]
    assert 0.0 <= precision_at_k(results, gold, 5) <= 1.0
    assert 0.0 <= recall_at_k(results, gold, 5) <= 1.0
    assert 0.0 <= mrr(results, gold) <= 1.0
    assert 0.0 <= ndcg_at_k(results, gold, 5) <= 1.0


def test_e18_harness_run_all_returns_metrics(seeded_client):
    """E18: EvalHarness.run_all over the 10 standard tasks returns metrics."""
    harness = EvalHarness(seeded_client._knowledge_db)

    def retrieval_fn(query):
        return [{"title": "no match"}]

    report = harness.run_all(retrieval_fn)
    assert report["aggregate"]["n_tasks"] == 10
    rendered = pretty_print(report)
    assert "Eval Harness" in rendered


def test_e18_scrub_text_replaces_email():
    """E18: scrub_text masks email addresses."""
    out = scrub_text("contact me at name@example.com please")
    assert "<email>" in out


def test_e18_scrub_text_replaces_ip():
    """E18: scrub_text masks IPv4 addresses."""
    out = scrub_text("ping 192.168.1.50 for details")
    assert "<ip>" in out


def test_e18_scrub_record_returns_copy():
    """E18: scrub_record returns a new dict, leaves input unchanged."""
    inp = {"body": "user@example.com", "id": 1}
    out = scrub_record(inp)
    assert out["body"] == "<email>"
    assert inp["body"] == "user@example.com"


def test_e18_regex_constants_compiled():
    """E18: EMAIL_RE / IP_RE / HOST_RE expose compiled regex patterns."""
    assert EMAIL_RE.search("user@example.com")
    assert IP_RE.search("10.0.0.1")
    assert HOST_RE.search("a.example.com")
