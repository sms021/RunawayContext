"""E16 — cross-system data map."""
from __future__ import annotations

import pytest

from runaway_context.cross_system import DataMap

pytestmark = pytest.mark.feature


def test_e16_add_source_idempotent(fresh_db):
    """E16: add_source is idempotent on (system, name)."""
    dm = DataMap(fresh_db)
    sid1 = dm.add_source(system="vista", name="JCCD", kind="table")
    sid2 = dm.add_source(system="vista", name="JCCD", kind="table",
                         description="updated")
    assert sid1 == sid2


def test_e16_add_mapping_links_sources(fresh_db):
    """E16: add_mapping creates an edge between two registered sources."""
    dm = DataMap(fresh_db)
    dm.add_source(system="vista", name="JCCD", kind="table")
    dm.add_source(system="procore", name="Commitments", kind="endpoint")
    mid = dm.add_mapping(
        from_system="vista", from_name="JCCD",
        to_system="procore", to_name="Commitments",
        join_on="contract_no",
    )
    assert mid > 0


def test_e16_trace_returns_steps(fresh_db):
    """E16: trace walks edges from a starting source."""
    dm = DataMap(fresh_db)
    dm.add_source(system="vista", name="JCCD", kind="table")
    dm.add_source(system="procore", name="Commitments", kind="endpoint")
    dm.add_mapping(from_system="vista", from_name="JCCD",
                   to_system="procore", to_name="Commitments")
    steps = dm.trace("vista", "JCCD", depth=2)
    assert steps


def test_e16_export_map(fresh_db, tmp_path):
    """E16: export_map writes a JSON file with sources + mappings."""
    dm = DataMap(fresh_db)
    dm.add_source(system="vista", name="JCCD", kind="table")
    out = tmp_path / "map.json"
    count = dm.export_map(out)
    assert out.exists()
    assert count >= 1


def test_e16_add_mapping_refuses_unknown_source(fresh_db):
    """E16: refuses mapping when an endpoint is not registered."""
    dm = DataMap(fresh_db)
    with pytest.raises(LookupError):
        dm.add_mapping(
            from_system="x", from_name="x",
            to_system="y", to_name="y",
        )
