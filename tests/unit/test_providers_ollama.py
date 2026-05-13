"""Coverage tests for ``runaway_context.embeddings.providers.ollama``.

The ollama provider is opt-in (HR-1). Importing the module without the
``network_opt_in['ollama']`` flag must raise :class:`ImportError`; with
the flag set the module imports successfully but ``embed`` requires the
network. Every test that exercises ``embed`` patches
:func:`urllib.request.urlopen` so no real socket is opened.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.feature

_MOD_NAME = "runaway_context.embeddings.providers.ollama"


def _set_opt_in(monkeypatch, tmp_path, enabled):
    """Patch ``Config.load`` so it returns a config with ollama opt-in toggled.

    Returns:
        The previous module if cached (also pops it from ``sys.modules``).
    """
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    from runaway_context.config import Config

    real_load = Config.load

    def fake_load(*args, **kwargs):
        """Return a Config with the ollama opt-in flag set per the closure."""
        cfg = real_load(*args, **kwargs)
        cfg.network_opt_in = {"ollama": True} if enabled else {}
        return cfg

    monkeypatch.setattr(Config, "load", staticmethod(fake_load))
    return sys.modules.pop(_MOD_NAME, None)


def _make_urlopen(payload):
    """Return a fake ``urlopen`` that yields *payload* as a JSON body.

    Returns:
        Callable suitable for ``patch('urllib.request.urlopen', side_effect=...)``.
    """
    def fake_urlopen(request, *args, **kwargs):
        """Return a context-manager-shaped response with a JSON body."""
        body = json.dumps(payload).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: None
        return resp
    return fake_urlopen


def test_ollama_blocked_without_opt_in(monkeypatch, tmp_path):
    """Importing ollama without the opt-in flag raises ImportError (HR-1)."""
    _set_opt_in(monkeypatch, tmp_path, enabled=False)
    with pytest.raises(ImportError):
        importlib.import_module(_MOD_NAME)


def test_ollama_allowed_with_opt_in(monkeypatch, tmp_path):
    """With the opt-in flag set, the ollama module imports successfully."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    assert hasattr(mod, "OllamaProvider")
    assert hasattr(mod, "build_provider")


def test_ollama_build_provider_rejects_bad_name(monkeypatch, tmp_path):
    """``build_provider`` rejects names that do not start with ``ollama-``."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.build_provider(name="openai-text-embedding-3-small")


def test_ollama_constructor_validation(monkeypatch, tmp_path):
    """``OllamaProvider`` refuses non-positive dim or timeout."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.OllamaProvider(dim=0)
    with pytest.raises(ValueError):
        mod.OllamaProvider(timeout=0.0)


def test_ollama_embed_mock(monkeypatch, tmp_path):
    """``embed`` POSTs to the loopback endpoint and parses the embedding list."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(model="nomic-embed-text", dim=4)
    payload = {"embedding": [0.1, 0.2, 0.3, 0.4]}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        out = prov.embed(["hello"])
    assert out == [[0.1, 0.2, 0.3, 0.4]]
    assert prov.provider_name == "ollama-nomic-embed-text"


def test_ollama_embed_rejects_string_input(monkeypatch, tmp_path):
    """``embed`` refuses a single string — it must be a list of strings."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)
    with pytest.raises(TypeError):
        prov.embed("hello")


def test_ollama_embed_rejects_non_string_element(monkeypatch, tmp_path):
    """Non-string elements inside the list are rejected with TypeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)
    with pytest.raises(TypeError):
        prov.embed([123])


def test_ollama_embed_dim_mismatch(monkeypatch, tmp_path):
    """A returned vector whose length disagrees with ``dim`` raises RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)
    payload = {"embedding": [0.1, 0.2]}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_ollama_embed_missing_embedding_field(monkeypatch, tmp_path):
    """A response missing the ``embedding`` list raises RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)
    with patch("urllib.request.urlopen", side_effect=_make_urlopen({"oops": True})):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_ollama_embed_non_json_body(monkeypatch, tmp_path):
    """A non-JSON body raises RuntimeError, not a ValueError leak."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)

    def garbage_urlopen(request, *args, **kwargs):
        """Return a response whose body is not valid JSON."""
        resp = MagicMock()
        resp.read.return_value = b"<<<not json>>>"
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=garbage_urlopen):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_ollama_embed_url_error(monkeypatch, tmp_path):
    """A URLError from urlopen becomes a RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OllamaProvider(dim=4)

    import urllib.error

    def boom(request, *args, **kwargs):
        """Raise URLError as urlopen would on a connection failure."""
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=boom):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_ollama_build_provider_via_factory(monkeypatch, tmp_path):
    """``build_provider`` parses the model out of ``ollama-<model>`` names."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.build_provider(name="ollama-nomic-embed-text")
    assert prov.model == "nomic-embed-text"
    assert prov.provider_name == "ollama-nomic-embed-text"
