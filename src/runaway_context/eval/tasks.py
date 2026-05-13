"""Ten synthetic retrieval tasks for the evaluation harness (E18).

Each task is a dict with:

* ``task_id`` — short stable identifier.
* ``query`` — natural-language query a user might type.
* ``expected_chunks`` — iterable of chunk titles considered "gold".
* ``expected_lessons`` — iterable of lesson titles considered "gold".
* ``description`` — human-readable purpose of the task (what it tests).
* ``tags`` — short labels (e.g. ``"fuzzy"``, ``"cross-project"``).

The harness scores any retrieval callable against this list. Gold items
are titles (case-insensitive comparison), not row ids, so the tasks stay
valid even when fixtures are regenerated.

Refuses:
    Returning an empty list — ``STANDARD_TASKS`` is required to have ten
    entries so coverage gates are predictable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


STANDARD_TASKS: List[Dict[str, Any]] = [
    {
        "task_id": "task-01-rule-lookup",
        "query": "case-variant folder check before creating new project",
        "expected_chunks": [],
        "expected_lessons": [
            "Check for case-variant folders before creating new projects",
        ],
        "description": "Direct rule lookup by topic keyword.",
        "tags": ["rule-lookup"],
    },
    {
        "task_id": "task-02-lesson-lookup",
        "query": "ABM release detection",
        "expected_chunks": [],
        "expected_lessons": [
            "ABM release detection uses releasedFromOrgDateTime, not 404",
        ],
        "description": "Lookup a lesson by partial substring match.",
        "tags": ["lesson-lookup"],
    },
    {
        "task_id": "task-03-cross-project",
        "query": "paycom roster fallback for departed",
        "expected_chunks": [],
        "expected_lessons": [
            "Roster paycom_eecode unreliable for departed; fuzzy lastname+nickname recovers",
        ],
        "description": "Cross-project search across paycom + field_staff.",
        "tags": ["cross-project"],
    },
    {
        "task_id": "task-04-archived-excluded",
        "query": "deprecated cron schedule (should not return)",
        "expected_chunks": [],
        "expected_lessons": [],
        "description": "Archived/superseded lessons must not surface by default.",
        "tags": ["archived-excluded"],
    },
    {
        "task_id": "task-05-fuzzy",
        "query": "AFP and pay app terminology",
        "expected_chunks": [],
        "expected_lessons": [
            "AFP → Pay App terminology",
        ],
        "description": "Fuzzy / synonym-style query.",
        "tags": ["fuzzy"],
    },
    {
        "task_id": "task-06-source-lookup",
        "query": "Microsoft UPN canonical email key",
        "expected_chunks": [],
        "expected_lessons": [
            "Microsoft UPN, not roster.email, is the canonical email key for cross-system identity",
        ],
        "description": "Lookup of a cross-system identity rule.",
        "tags": ["source-lookup"],
    },
    {
        "task_id": "task-07-multi-project",
        "query": "Master Pipeline board relation columns",
        "expected_chunks": [],
        "expected_lessons": [
            "Monday board-relation columns have a connected-boards allow-list; 'not in connected boards' error pattern",
        ],
        "description": "Pipeline / Monday cross-project search.",
        "tags": ["multi-project"],
    },
    {
        "task_id": "task-08-severity-filter",
        "query": "critical lesson about audit hold age",
        "expected_chunks": [],
        "expected_lessons": [
            "QA audit hold age must come from source dates, not snapshot diffs",
        ],
        "description": "Severity-aware lookup.",
        "tags": ["severity"],
    },
    {
        "task_id": "task-09-tooling-rule",
        "query": "Chrome scraper exclusivity",
        "expected_chunks": [],
        "expected_lessons": [
            "Chrome only supports one scraper at a time",
        ],
        "description": "Single-project tooling rule.",
        "tags": ["tooling"],
    },
    {
        "task_id": "task-10-empty-result",
        "query": "this query intentionally matches nothing in the fixture",
        "expected_chunks": [],
        "expected_lessons": [],
        "description": "Negative test: zero gold, retrieval should not falsely match.",
        "tags": ["empty"],
    },
]


def load_tasks(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return a tasks list. If ``path`` is None, returns ``STANDARD_TASKS``.

    Args:
        path: Optional path to a JSON file containing a list of task dicts.

    Returns:
        List of task dicts.

    Raises:
        FileNotFoundError: when ``path`` is given but does not exist.
        ValueError: when the JSON payload is not a list.

    Refuses:
        Returning a partial list when the file exists but is malformed.
    """
    if path is None:
        return [dict(t) for t in STANDARD_TASKS]
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    payload = json.loads(p.read_text())
    if not isinstance(payload, list):
        raise ValueError(
            "tasks file at {p!r} must contain a JSON list".format(p=str(p))
        )
    out: List[Dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("task entries must be objects/dicts")
        out.append(entry)
    return out


assert len(STANDARD_TASKS) == 10, "STANDARD_TASKS must contain exactly 10 entries"
