"""Coverage tests for ``runaway_context.embeddings.providers.openai``.

The OpenAI provider is opt-in (HR-1). Without the
``network_opt_in['openai']`` flag, importing the module must raise
:class:`ImportError`. With the flag set, ``embed`` requires a working
HTTP path and an ``OPENAI_API_KEY`` in the environment. All tests that
exercise ``embed`` patch :func:`urllib.request.urlopen` so no real socket
is opened.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.feature

_MOD_NAME = "runaway_context.embeddings.providers.openai"


def _set_opt_in(monkeypatch, tmp_path, enabled):
    """Patch ``Config.load`` so it returns a config with the openai opt-in toggled.

    Returns:
        ``None``. Pops any cached copy of the openai provider module.
    """
    monkeypatch.setenv("RC_KS_DIR", str(tmp_path))
    from runaway_context.config import Config

    real_load = Config.load

    def fake_load(*args, **kwargs):
        """Return a Config with the openai opt-in flag set per the closure."""
        cfg = real_load(*args, **kwargs)
        cfg.network_opt_in = {"openai": True} if enabled else {}
        return cfg

    monkeypatch.setattr(Config, "load", staticmethod(fake_load))
    sys.modules.pop(_MOD_NAME, None)


def _make_urlopen(payload):
    """Return a fake ``urlopen`` returning *payload* as a JSON body.

    Returns:
        Callable suitable for ``patch('urllib.request.urlopen', side_effect=...)``.
    """
    def fake_urlopen(request, *args, **kwargs):
        """Return a context-manager-shaped response wrapping *payload*."""
        body = json.dumps(payload).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda self_: self_
        resp.__exit__ = lambda *a: None
        return resp
    return fake_urlopen


def test_openai_blocked_without_opt_in(monkeypatch, tmp_path):
    """Importing openai without the opt-in flag raises ImportError (HR-1)."""
    _set_opt_in(monkeypatch, tmp_path, enabled=False)
    with pytest.raises(ImportError):
        importlib.import_module(_MOD_NAME)


def test_openai_allowed_with_opt_in(monkeypatch, tmp_path):
    """With the opt-in flag set, the openai module imports successfully."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    mod = importlib.import_module(_MOD_NAME)
    assert hasattr(mod, "OpenAIProvider")
    assert hasattr(mod, "build_provider")


def test_openai_missing_api_key_refuses(monkeypatch, tmp_path):
    """Constructing the provider without ``OPENAI_API_KEY`` raises ValueError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.OpenAIProvider()


def test_openai_unknown_model_refused(monkeypatch, tmp_path):
    """Unknown model names are rejected before the API key is checked."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.OpenAIProvider(model="text-embedding-99-nonexistent")


def test_openai_constructor_timeout_validation(monkeypatch, tmp_path):
    """Non-positive timeouts are refused with ValueError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.OpenAIProvider(timeout=0.0)


def test_openai_build_provider_rejects_bad_name(monkeypatch, tmp_path):
    """``build_provider`` rejects names that do not start with ``openai-``."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    with pytest.raises(ValueError):
        mod.build_provider(name="voyage-2")


def test_openai_build_provider_via_factory(monkeypatch, tmp_path):
    """``build_provider`` strips the ``openai-`` prefix and yields a provider."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.build_provider(name="openai-text-embedding-3-small")
    assert prov.model == "text-embedding-3-small"
    assert prov.dim == 1536


def test_openai_embed_mock(monkeypatch, tmp_path):
    """``embed`` parses the ``data[*].embedding`` payload from the API."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    fake_vec = [0.1, 0.2] + [0.0] * (prov.dim - 2)
    payload = {"data": [{"embedding": fake_vec}]}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        out = prov.embed(["hello"])
    assert len(out) == 1
    assert len(out[0]) == prov.dim
    assert abs(out[0][0] - 0.1) < 1e-6


def test_openai_embed_rejects_string_input(monkeypatch, tmp_path):
    """``embed`` refuses a single string — it must be a list of strings."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    with pytest.raises(TypeError):
        prov.embed("hello")


def test_openai_embed_rejects_non_string_element(monkeypatch, tmp_path):
    """Non-string elements inside the list raise TypeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    with pytest.raises(TypeError):
        prov.embed([42])


def test_openai_embed_row_count_mismatch(monkeypatch, tmp_path):
    """A response whose ``data`` length disagrees with input count fails."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    payload = {"data": []}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_openai_embed_missing_embedding(monkeypatch, tmp_path):
    """A row without an ``embedding`` list raises RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    payload = {"data": [{"no_embedding_here": True}]}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_openai_embed_dim_mismatch(monkeypatch, tmp_path):
    """A returned vector whose length disagrees with ``dim`` raises RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")
    payload = {"data": [{"embedding": [0.1, 0.2]}]}
    with patch("urllib.request.urlopen", side_effect=_make_urlopen(payload)):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])


def test_openai_embed_non_json_body(monkeypatch, tmp_path):
    """A non-JSON body raises RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")

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


def test_openai_embed_url_error(monkeypatch, tmp_path):
    """A URLError from urlopen becomes a RuntimeError."""
    _set_opt_in(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mod = importlib.import_module(_MOD_NAME)
    prov = mod.OpenAIProvider(model="text-embedding-3-small")

    import urllib.error

    def boom(request, *args, **kwargs):
        """Raise URLError as urlopen would on a connection failure."""
        raise urllib.error.URLError("dns failure")

    with patch("urllib.request.urlopen", side_effect=boom):
        with pytest.raises(RuntimeError):
            prov.embed(["hello"])
