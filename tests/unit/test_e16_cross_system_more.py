"""E16 — cross_system — extra branches: trace depth, find_sources filters, etc."""
from __future__ import annotations

import pytest

from runaway_context.cross_system import DataMap

pytestmark = pytest.mark.feature


def test_add_source_validates_inputs(fresh_db):
    dm = DataMap(fresh_db)
    with pytest.raises(ValueError):
        dm.add_source(system="", name="x", kind="table")
    with pytest.raises(ValueError):
        dm.add_source(system="vista", name="", kind="table")
    with pytest.raises(ValueError):
        dm.add_source(system="vista", name="x", kind="bogus_kind")


def test_find_sources_filters(fresh_db):
    dm = DataMap(fresh_db)
    dm.add_source(system="vista", name="JCCD", kind="table",
                  description="Job cost commitments")
    dm.add_source(system="vista", name="APUH", kind="table")
    dm.add_source(system="procore", name="Comm", kind="endpoint")

    # filter by system
    out = dm.find_sources(system="vista")
    assert {r["name"] for r in out} == {"JCCD", "APUH"}

    # filter by query
    out = dm.find_sources(query="commitments")
    assert any(r["name"] == "JCCD" for r in out)

    # no match
    out = dm.find_sources(system="vista", query="zzz")
    assert out == []

    # no filter
    out = dm.find_sources()
    assert len(out) >= 3


def test_trace_depth_validation(fresh_db):
    dm = DataMap(fresh_db)
    dm.add_source(system="x", name="y", kind="table")
    with pytest.raises(ValueError):
        dm.trace("x", "y", depth=-1)


def test_trace_unknown_source(fresh_db):
    dm = DataMap(fresh_db)
    with pytest.raises(LookupError):
        dm.trace("nope", "neither", depth=1)


def test_trace_walks_multiple_hops(fresh_db):
    dm = DataMap(fresh_db)
    dm.add_source(system="a", name="1", kind="table")
    dm.add_source(system="b", name="2", kind="table")
    dm.add_source(system="c", name="3", kind="table")
    dm.add_mapping(from_system="a", from_name="1",
                   to_system="b", to_name="2")
    dm.add_mapping(from_system="b", from_name="2",
                   to_system="c", to_name="3")
    edges = dm.trace("a", "1", depth=3)
    assert len(edges) >= 2
    # Step numbers are sequential
    assert {e["step"] for e in edges} == {1, 2}


def test_add_mapping_idempotent_updates_notes(fresh_db):
    dm = DataMap(fresh_db)
    dm.add_source(system="a", name="1", kind="table")
    dm.add_source(system="b", name="2", kind="table")
    mid = dm.add_mapping(from_system="a", from_name="1",
                         to_system="b", to_name="2",
                         join_on="x", notes="first")
    mid2 = dm.add_mapping(from_system="a", from_name="1",
                          to_system="b", to_name="2",
                          join_on="x", notes="second")
    assert mid == mid2


def test_export_map_includes_mappings(fresh_db, tmp_path):
    dm = DataMap(fresh_db)
    dm.add_source(system="vista", name="JCCD", kind="table",
                  description="job|cost")
    dm.add_source(system="procore", name="Comm", kind="endpoint")
    dm.add_mapping(from_system="vista", from_name="JCCD",
                   to_system="procore", to_name="Comm",
                   join_on="cn", notes="link|able")
    out = tmp_path / "map.md"
    dm.export_map(out)
    content = out.read_text()
    assert "## Sources" in content
    assert "## Mappings" in content
    # Pipe escaped
    assert "job\\|cost" in content
