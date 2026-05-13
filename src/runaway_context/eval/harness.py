"""Evaluation harness (E18, P6).

Decision-driver for retrieval changes: ship a retrieval tweak only if
``EvalHarness.run_all`` produces non-regression on aggregate MRR@5 /
NDCG@5 against the 10 reference tasks (see ``tasks.STANDARD_TASKS``).

The harness is intentionally retrieval-agnostic: the caller passes a
``retrieval_fn(query: str) -> list[dict]`` callable. Each result dict
must include either a ``title`` or a ``name`` field — we match the
``expected_*`` titles against that.

Refuses:
    Running tasks without a callable ``retrieval_fn`` (raises
    ``TypeError``).
"""

from __future__ import annotations

import math
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


def _norm(value: Optional[str]) -> str:
    """Normalise a title for comparison (lowercase, collapsed whitespace).

    Returns:
        Stripped + lowercased + whitespace-collapsed string.

    Refuses:
        Nothing — ``None`` becomes the empty string.
    """
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _result_title(item: Dict[str, Any]) -> str:
    """Pull a comparable title from a retrieval result dict.

    Returns:
        Normalised title string. Falls back to ``"id:<id>"`` if no title.

    Refuses:
        Nothing — empty results return ``""``.
    """
    if not isinstance(item, dict):
        return ""
    for key in ("title", "name", "topic"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return _norm(val)
    if "id" in item:
        return "id:{id}".format(id=item["id"])
    return ""


def precision_at_k(
    results: Sequence[Any], gold: Iterable[str], k: int = 5
) -> float:
    """Precision@k — fraction of the top-k results that are in ``gold``.

    Args:
        results: Sequence of retrieval result dicts (or anything from which
            :func:`_result_title` can extract a title).
        gold: Iterable of gold titles.
        k: Cutoff (default 5).

    Returns:
        Float in [0, 1]. Returns 0.0 when ``k <= 0`` or ``results`` is empty.

    Refuses:
        Nothing.
    """
    if k <= 0:
        return 0.0
    top = list(results)[:k]
    if not top:
        return 0.0
    gold_set = {_norm(g) for g in gold if g is not None}
    if not gold_set:
        # No gold → vacuously perfect when no results, zero precision otherwise.
        return 0.0
    hits = sum(1 for r in top if _result_title(r) in gold_set)
    return hits / float(min(k, len(top)))


def recall_at_k(
    results: Sequence[Any], gold: Iterable[str], k: int = 5
) -> float:
    """Recall@k — fraction of ``gold`` items present in the top-k results.

    Returns:
        Float in [0, 1]. Returns 1.0 when there is no gold (vacuous recall).

    Refuses:
        Nothing.
    """
    gold_set = {_norm(g) for g in gold if g is not None}
    if not gold_set:
        return 1.0
    top = list(results)[: max(0, k)]
    hits = sum(1 for r in top if _result_title(r) in gold_set)
    return hits / float(len(gold_set))


def mrr(results: Sequence[Any], gold: Iterable[str]) -> float:
    """Mean reciprocal rank — 1 / rank of the first gold hit (0 if none).

    Args:
        results: Ordered sequence of results.
        gold: Iterable of gold titles.

    Returns:
        Float in [0, 1].

    Refuses:
        Nothing.
    """
    gold_set = {_norm(g) for g in gold if g is not None}
    if not gold_set:
        return 0.0
    for i, r in enumerate(results, start=1):
        if _result_title(r) in gold_set:
            return 1.0 / i
    return 0.0


def ndcg_at_k(
    results: Sequence[Any], gold: Iterable[str], k: int = 5
) -> float:
    """NDCG@k with binary relevance (1 if result in gold, else 0).

    Returns:
        Float in [0, 1].

    Refuses:
        Nothing.
    """
    if k <= 0:
        return 0.0
    gold_set = {_norm(g) for g in gold if g is not None}
    if not gold_set:
        return 0.0
    top = list(results)[:k]
    if not top:
        return 0.0
    # DCG with rel in {0, 1}, log2(i+1) discount (i is 1-indexed).
    dcg = 0.0
    for i, r in enumerate(top, start=1):
        rel = 1.0 if _result_title(r) in gold_set else 0.0
        if rel:
            dcg += rel / math.log2(i + 1)
    # IDCG: best possible — gold items in the first len(gold) positions.
    ideal_n = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_n + 1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


class EvalHarness:
    """Runs synthetic retrieval tasks and aggregates metrics.

    The harness is intentionally retrieval-agnostic: callers pass any
    ``retrieval_fn(query) -> list[dict]`` callable. Results are matched
    against gold titles (case-insensitive).
    """

    def __init__(self, knowledge_db: Path) -> None:
        """Bind to a v3-migrated ``knowledge.db``.

        The DB path is kept as metadata so reports can reference it; the
        harness itself does not require an open connection (retrieval is
        passed in).

        Returns:
            None.

        Refuses:
            Nothing.
        """
        self._db_path = Path(knowledge_db)

    @property
    def knowledge_db(self) -> Path:
        """Return the bound knowledge DB path.

        Returns:
            ``Path`` to ``knowledge.db``.

        Refuses:
            Nothing.
        """
        return self._db_path

    def run_task(
        self,
        task: Dict[str, Any],
        retrieval_fn: Callable[[str], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Run a single task and return its metrics dict.

        Args:
            task: Task dict (see ``tasks.STANDARD_TASKS`` for shape).
            retrieval_fn: Callable(query) -> list of result dicts.

        Returns:
            ``{task_id, query, n_results, latency_ms,
               precision_at_5, recall_at_5, mrr, ndcg_at_5,
               gold_titles, top_titles}``.

        Raises:
            TypeError: when ``retrieval_fn`` is not callable.
            KeyError: when ``task`` is missing ``query``.

        Refuses:
            Calling a non-callable retrieval function.
        """
        if not callable(retrieval_fn):
            raise TypeError("retrieval_fn must be callable")
        query = task["query"]
        gold_titles = list(task.get("expected_chunks", [])) + list(
            task.get("expected_lessons", [])
        )
        start = time.perf_counter()
        results = retrieval_fn(query)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if results is None:
            results = []
        if not isinstance(results, list):
            results = list(results)
        top_titles = [_result_title(r) for r in results[:5]]
        return {
            "task_id": task.get("task_id"),
            "query": query,
            "tags": list(task.get("tags", [])),
            "n_results": len(results),
            "latency_ms": round(elapsed_ms, 3),
            "precision_at_5": precision_at_5_value(results, gold_titles),
            "recall_at_5": recall_at_k(results, gold_titles, k=5),
            "mrr": mrr(results, gold_titles),
            "ndcg_at_5": ndcg_at_k(results, gold_titles, k=5),
            "gold_titles": [_norm(g) for g in gold_titles],
            "top_titles": top_titles,
        }

    def run_all(
        self,
        retrieval_fn: Callable[[str], List[Dict[str, Any]]],
        tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Run every task and return per-task + aggregated metrics.

        Args:
            retrieval_fn: Callable(query) -> list of result dicts.
            tasks: Optional override list. Falls back to ``STANDARD_TASKS``.

        Returns:
            ``{knowledge_db, tasks: [...], aggregate: {...}}``.

        Raises:
            TypeError: when ``retrieval_fn`` is not callable.

        Refuses:
            Calling a non-callable retrieval function.
        """
        if not callable(retrieval_fn):
            raise TypeError("retrieval_fn must be callable")
        # Import lazily so this module stays free of circular import risk.
        from runaway_context.eval.tasks import STANDARD_TASKS

        task_list = tasks if tasks is not None else [dict(t) for t in STANDARD_TASKS]
        per_task: List[Dict[str, Any]] = []
        for t in task_list:
            per_task.append(self.run_task(t, retrieval_fn))

        def _mean(vals: List[float]) -> float:
            return statistics.fmean(vals) if vals else 0.0

        aggregate = {
            "n_tasks": len(per_task),
            "mean_precision_at_5": _mean([m["precision_at_5"] for m in per_task]),
            "mean_recall_at_5": _mean([m["recall_at_5"] for m in per_task]),
            "mean_mrr": _mean([m["mrr"] for m in per_task]),
            "mean_ndcg_at_5": _mean([m["ndcg_at_5"] for m in per_task]),
            "mean_latency_ms": _mean([m["latency_ms"] for m in per_task]),
        }
        return {
            "knowledge_db": str(self._db_path),
            "tasks": per_task,
            "aggregate": aggregate,
        }


def precision_at_5_value(
    results: Sequence[Any], gold: Iterable[str]
) -> float:
    """Shorthand for ``precision_at_k(results, gold, 5)``.

    Returns:
        Float in [0, 1].

    Refuses:
        Nothing.
    """
    return precision_at_k(results, gold, k=5)


def pretty_print(report: Dict[str, Any]) -> str:
    """Format a report from :meth:`EvalHarness.run_all` as plain text.

    Args:
        report: The dict returned by :meth:`EvalHarness.run_all`.

    Returns:
        Multi-line human-readable summary.

    Refuses:
        Reports with missing ``aggregate`` raise ``KeyError`` rather than
        silently returning a stub.
    """
    agg = report["aggregate"]
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("RunawayContext Eval Harness")
    lines.append("=" * 60)
    lines.append("DB:            {p}".format(p=report.get("knowledge_db", "n/a")))
    lines.append("Tasks run:     {n}".format(n=agg["n_tasks"]))
    lines.append("")
    lines.append("Aggregate metrics:")
    lines.append("  precision@5:  {v:.3f}".format(v=agg["mean_precision_at_5"]))
    lines.append("  recall@5:     {v:.3f}".format(v=agg["mean_recall_at_5"]))
    lines.append("  MRR:          {v:.3f}".format(v=agg["mean_mrr"]))
    lines.append("  NDCG@5:       {v:.3f}".format(v=agg["mean_ndcg_at_5"]))
    lines.append("  latency (ms): {v:.2f}".format(v=agg["mean_latency_ms"]))
    lines.append("")
    lines.append("Per-task:")
    for t in report.get("tasks", []):
        lines.append(
            "  {tid:<26}  P@5={p:.2f}  R@5={r:.2f}  MRR={m:.2f}  NDCG@5={n:.2f}".format(
                tid=t.get("task_id") or "?",
                p=t.get("precision_at_5", 0.0),
                r=t.get("recall_at_5", 0.0),
                m=t.get("mrr", 0.0),
                n=t.get("ndcg_at_5", 0.0),
            )
        )
    lines.append("=" * 60)
    return "\n".join(lines)
