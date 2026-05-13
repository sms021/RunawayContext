"""Evaluation harness package (E18, P6).

Public surface::

    from runaway_context.eval import EvalHarness, STANDARD_TASKS, scrub_text

* :class:`EvalHarness` — runs synthetic retrieval tasks against any
  retrieval callable and returns precision/recall/MRR/NDCG metrics.
* :data:`STANDARD_TASKS` — ten built-in synthetic tasks covering rule
  lookup, lesson lookup, cross-project search, archived-exclusion,
  fuzzy match, etc.
* :func:`scrub_text` — PII scrubbing helper used before any export that
  could include session transcripts.

Refuses:
    Importing optional retrieval providers (HR-1) — none of the eval
    helpers reach the network.
"""

from runaway_context.eval.harness import (  # noqa: F401
    EvalHarness,
    mrr,
    ndcg_at_k,
    precision_at_k,
    pretty_print,
    recall_at_k,
)
from runaway_context.eval.scrub import (  # noqa: F401
    EMAIL_RE,
    HOST_RE,
    IP_RE,
    scrub_record,
    scrub_text,
)
from runaway_context.eval.tasks import (  # noqa: F401
    STANDARD_TASKS,
    load_tasks,
)

__all__ = [
    "EvalHarness",
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "ndcg_at_k",
    "pretty_print",
    "STANDARD_TASKS",
    "load_tasks",
    "scrub_text",
    "scrub_record",
    "EMAIL_RE",
    "IP_RE",
    "HOST_RE",
]
