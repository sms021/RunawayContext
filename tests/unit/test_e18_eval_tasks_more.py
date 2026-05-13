"""E18 — eval/tasks load_tasks paths."""
from __future__ import annotations

import json

import pytest

from runaway_context.eval.tasks import STANDARD_TASKS, load_tasks


def test_load_tasks_from_file(tmp_path):
    """load_tasks reads a JSON list of task dicts."""
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps([
        {"task_id": "x", "query": "q", "expected_chunks": [],
         "expected_lessons": [], "tags": []},
    ]))
    out = load_tasks(p)
    assert out[0]["task_id"] == "x"


def test_load_tasks_missing_file(tmp_path):
    """load_tasks raises FileNotFoundError when path doesn't exist."""
    with pytest.raises(FileNotFoundError):
        load_tasks(tmp_path / "nope.json")


def test_load_tasks_not_list(tmp_path):
    """load_tasks raises ValueError when payload isn't a list."""
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(ValueError):
        load_tasks(p)


def test_load_tasks_non_dict_entry(tmp_path):
    """load_tasks raises ValueError when list contains non-dict entries."""
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps([{"x": 1}, "not a dict"]))
    with pytest.raises(ValueError):
        load_tasks(p)


def test_standard_tasks_are_dicts():
    for entry in STANDARD_TASKS:
        assert isinstance(entry, dict)
        assert "task_id" in entry
