"""E5 — slug lifecycle (register, alias, deprecate, merge, resolve)."""
from __future__ import annotations

import pytest

from runaway_context.errors import InvalidProjectSlug
from runaway_context.slugs_lifecycle import SlugRegistry

pytestmark = pytest.mark.feature


def test_e05_register_idempotent(fresh_db):
    """E5: registering the same slug twice is a no-op."""
    reg = SlugRegistry(fresh_db)
    reg.register("alpha")
    reg.register("alpha", description="second registration")
    assert "alpha" in reg.list_active()


def test_e05_alias_resolves_to_canonical(fresh_db):
    """E5: alias() points an alias slug at a canonical slug."""
    reg = SlugRegistry(fresh_db)
    reg.register("alpha")
    reg.alias("alfa", "alpha")
    assert reg.resolve("alfa") == "alpha"
    assert reg.is_valid("alfa") is True


def test_e05_deprecate_keeps_queryable(fresh_db):
    """E5: deprecate() blocks writes but the slug remains in the registry."""
    reg = SlugRegistry(fresh_db)
    reg.register("legacy")
    reg.deprecate("legacy", reason="superseded by alpha")
    rows = {r["slug"]: r for r in reg.list_all()}
    assert rows["legacy"]["status"] == "deprecated"
    assert reg.resolve("legacy") is None


def test_e05_merge_marks_status(fresh_db):
    """E5: merge() folds the from-slug into the to-slug as an alias."""
    reg = SlugRegistry(fresh_db)
    reg.register("alpha")
    reg.register("alfa_old")
    reg.merge("alfa_old", "alpha")
    rows = {r["slug"]: r for r in reg.list_all()}
    assert rows["alfa_old"]["status"] == "merged"
    assert reg.resolve("alfa_old") == "alpha"


def test_e05_invalid_slug_refused(fresh_db):
    """E5: malformed slugs are refused with InvalidProjectSlug."""
    reg = SlugRegistry(fresh_db)
    with pytest.raises(InvalidProjectSlug):
        reg.register("BadCase")
    with pytest.raises(InvalidProjectSlug):
        reg.register("has spaces")
