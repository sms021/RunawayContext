"""Voyage AI embedding provider (opt-in).

HR-1: this module imports ``urllib`` at module level. The import is
GUARDED behind a runtime check on
:attr:`runaway_context.config.Config.network_opt_in`; with the flag False
the import is never reached and the module raises :class:`ImportError`.

The API key is read from the environment (``VOYAGE_API_KEY``) at provider
construction.

Refuses:
    Loading at all unless ``Config.network_opt_in['voyage']`` is True.
    Calling the API without a ``VOYAGE_API_KEY`` in the environment.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from runaway_context.config import Config


_cfg = Config.load()
if not bool(_cfg.network_opt_in.get("voyage", False)):
    raise ImportError(
        "voyage provider requires Config.network_opt_in['voyage'] = True (HR-1). "
        "Set the flag in your install's config.json before importing this module."
    )

# Network import allowed past this point only because the HR-1 opt-in guard
# above gates module import on an explicit user-set flag.
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402


_API_URL = "https://api.voyageai.com/v1/embeddings"
_MODEL_DIMS = {
    "voyage-2": 1024,
    "voyage-large-2": 1536,
}


class VoyageProvider:
    """Embed via the Voyage ``/v1/embeddings`` endpoint.

    Returns:
        One vector per input text from :meth:`embed`.

    Raises:
        RuntimeError: when the API call fails or returns unexpected shape.
        ValueError: when the API key is missing or the model is unknown.

    Refuses:
        Embedding without an API key.
    """

    def __init__(self, *, model: str = "voyage-2",
                 api_key: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        """Construct the provider for *model*.

        Refuses:
            Unknown models, missing API key, non-positive timeout.
        """
        if model not in _MODEL_DIMS:
            raise ValueError(
                f"unknown Voyage model: {model!r} "
                f"(known: {sorted(_MODEL_DIMS)})"
            )
        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise ValueError("VOYAGE_API_KEY is not set in the environment")
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        self.model = model
        self.dim = _MODEL_DIMS[model]
        self.timeout = float(timeout)
        self.provider_name = model
        self._api_key = key

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed each text in *texts* via the Voyage embeddings API.

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
        payload = json.dumps({
            "model": self.model,
            "input": list(texts),
        }).encode("utf-8")
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
            raise RuntimeError(f"voyage embedding request failed: {exc}") from exc
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise RuntimeError(f"voyage returned non-JSON body: {exc}") from exc
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError(
                f"voyage returned unexpected payload shape: {data!r}"
            )
        out: List[List[float]] = []
        for row in rows:
            vec = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(vec, list):
                raise RuntimeError("voyage row missing 'embedding' list")
            if len(vec) != self.dim:
                raise RuntimeError(
                    f"voyage returned dim={len(vec)} but provider expects {self.dim}"
                )
            out.append([float(x) for x in vec])
        return out


def build_provider(
    name: str = "voyage-2",
    config: Optional[Config] = None,
) -> VoyageProvider:
    """Construct a :class:`VoyageProvider` from a registry id.

    Returns:
        A configured :class:`VoyageProvider`.

    Raises:
        ValueError: when *name* is not a Voyage model id.

    Refuses:
        Constructing a provider for a non-voyage name.
    """
    if name not in _MODEL_DIMS:
        raise ValueError(f"voyage provider cannot serve {name!r}")
    return VoyageProvider(model=name)


__all__ = ["VoyageProvider", "build_provider"]
