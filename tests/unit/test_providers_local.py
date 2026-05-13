"""Coverage tests for ``runaway_context.embeddings.providers.local``.

``local.py`` is the no-network fallback re-export of
:class:`LocalDeterministicProvider`. It must import cleanly even when
:attr:`Config.network_opt_in` is empty (HR-1) and its
:func:`build_provider` factory must produce a provider that obeys the
:class:`Provider` protocol.
"""
from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = pytest.mark.feature


def _force_reload_local():
    """Drop any cached copy of ``providers.local`` and re-import it.

    Returns:
        The freshly imported module.
    """
    sys.modules.pop("runaway_context.embeddings.providers.local", None)
    return importlib.import_module("runaway_context.embeddings.providers.local")


def test_local_import_succeeds_without_opt_in(monkeypatch, tmp_path):
    """``local.py`` has no network imports and loads with empty opt-in."""
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    from runaway_context.config import Config

    real_load = Config.load

    def fake_load(*args, **kwargs):
        """Return a Config whose ``network_opt_in`` map is empty."""
        cfg = real_load(*args, **kwargs)
        cfg.network_opt_in = {}
        return cfg

    monkeypatch.setattr(Config, "load", staticmethod(fake_load))
    module = _force_reload_local()
    assert hasattr(module, "build_provider")


def test_local_build_provider(monkeypatch, tmp_path):
    """``build_provider`` returns an object exposing the Provider contract."""
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    module = _force_reload_local()
    prov = module.build_provider()
    assert hasattr(prov, "embed")
    assert hasattr(prov, "dim")
    assert hasattr(prov, "provider_name")
    assert prov.dim == 384


def test_local_build_provider_mpnet_dim(monkeypatch, tmp_path):
    """Names containing ``mpnet`` yield the 768-dim variant."""
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    module = _force_reload_local()
    prov = module.build_provider(name="sentence-transformers-mpnet-base-v2")
    assert prov.dim == 768
    assert prov.provider_name == "sentence-transformers-mpnet-base-v2"


def test_local_embed_returns_floats(monkeypatch, tmp_path):
    """``embed`` returns one float vector per text, of the declared dim."""
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    module = _force_reload_local()
    prov = module.build_provider()
    out = prov.embed(["hello"])
    assert isinstance(out, list)
    assert len(out) == 1
    assert len(out[0]) == prov.dim
    assert all(isinstance(x, float) for x in out[0])
