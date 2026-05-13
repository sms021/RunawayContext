"""OpenAI embedding provider (opt-in).

HR-1: this module imports ``urllib`` at module level. The import is
GUARDED behind a runtime check on
:attr:`runaway_context.config.Config.network_opt_in`; with the flag False
the import is never reached and the module raises :class:`ImportError`.

The API key is read from the environment (``OPENAI_API_KEY``) at provider
construction. We do not persist the key in config.

Refuses:
    Loading at all unless ``Config.network_opt_in['openai']`` is True.
    Calling the API without an ``OPENAI_API_KEY`` in the environment.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from runaway_context.config import Config


_cfg = Config.load()
if not bool(_cfg.network_opt_in.get("openai", False)):
    raise ImportError(
        "openai provider requires Config.network_opt_in['openai'] = True (HR-1). "
        "Set the flag in your install's config.json before importing this module."
    )

# Network import allowed past this point only because the HR-1 opt-in guard
# above gates module import on an explicit user-set flag.
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402


_API_URL = "https://api.openai.com/v1/embeddings"
_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


class OpenAIProvider:
    """Embed via the OpenAI ``/v1/embeddings`` endpoint.

    Returns:
        One vector per input text from :meth:`embed`.

    Raises:
        RuntimeError: when the API call fails or returns an unexpected
            shape.
        ValueError: when the API key is missing.

    Refuses:
        Embedding without an API key.
    """

    def __init__(self, *, model: str = "text-embedding-3-small",
                 api_key: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        """Construct the provider for *model*.

        Refuses:
            Unknown models, missing API key, non-positive timeout.
        """
        if model not in _MODEL_DIMS:
            raise ValueError(
                f"unknown OpenAI embedding model: {model!r} "
                f"(known: {sorted(_MODEL_DIMS)})"
            )
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "OPENAI_API_KEY is not set in the environment"
            )
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        self.model = model
        self.dim = _MODEL_DIMS[model]
        self.timeout = float(timeout)
        self.provider_name = f"openai-{model}"
        self._api_key = key

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed each text in *texts* via the OpenAI embeddings API.

        Returns:
            One vector per input text.

        Raises:
            RuntimeError: on HTTP / shape errors.
            TypeError: when *texts* is not a list of strings.

        Refuses:
            Returning partial output.
        """
        if isinstance(texts, str):
            raise TypeError("texts must be a list of strings, not a single string")
        for text in texts:
            if not isinstance(text, str):
                raise TypeError(f"expected str, got {type(text).__name__}")
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        request = urllib.request.Request(
            _API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
            body = response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"openai embedding request failed: {exc}") from exc
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise RuntimeError(f"openai returned non-JSON body: {exc}") from exc
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError(
                f"openai returned unexpected payload shape: {data!r}"
            )
        out: List[List[float]] = []
        for row in rows:
            vec = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(vec, list):
                raise RuntimeError("openai row missing 'embedding' list")
            if len(vec) != self.dim:
                raise RuntimeError(
                    f"openai returned dim={len(vec)} but provider expects {self.dim}"
                )
            out.append([float(x) for x in vec])
        return out


def build_provider(
    name: str = "openai-text-embedding-3-small",
    config: Optional[Config] = None,
) -> OpenAIProvider:
    """Construct an :class:`OpenAIProvider` from a registry id.

    Returns:
        A configured :class:`OpenAIProvider`.

    Raises:
        ValueError: when *name* does not start with ``openai-``.

    Refuses:
        Constructing a provider for a non-openai name.
    """
    if not name.startswith("openai-"):
        raise ValueError(f"openai provider cannot serve {name!r}")
    model = name[len("openai-"):]
    return OpenAIProvider(model=model)


__all__ = ["OpenAIProvider", "build_provider"]
