from __future__ import annotations

import os
from typing import Any, Sequence

from ots.embedding_providers.base import EmbeddingProvider
from ots.embedding_providers.utils import normalize_vectors

DEFAULT_OLLAMA_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_OLLAMA_EMBEDDING_DIMENSIONS = 768
QWEN3_EMBEDDING_MODEL = "qwen3-embedding"
QWEN3_EMBEDDING_DIMENSIONS = 4096


class OllamaEmbeddingProvider(EmbeddingProvider):
    provider_key = "ollama"

    @classmethod
    def default_model(cls) -> str:
        return DEFAULT_OLLAMA_EMBEDDING_MODEL

    @classmethod
    def default_dimensions(cls, model: str) -> int:
        if model == QWEN3_EMBEDDING_MODEL:
            return QWEN3_EMBEDDING_DIMENSIONS
        return DEFAULT_OLLAMA_EMBEDDING_DIMENSIONS

    @classmethod
    def default_options(cls) -> dict[str, Any]:
        return {
            "host": os.getenv("OTS_OLLAMA_HOST") or None,
        }

    @classmethod
    def normalize_options(cls, options: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in options.items() if key == "host" and value is not None}

    def __init__(
        self,
        *,
        model: str,
        host: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        try:
            import ollama
        except ImportError as exc:
            raise RuntimeError(
                "Ollama embedding support requires the ollama package. "
                "Run `pipenv install` to sync dependencies."
            ) from exc

        self.model = model
        self.dimensions = dimensions
        self._ollama = ollama
        self._client = ollama.Client(host=host) if host else None

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        embed = self._client.embed if self._client else self._ollama.embed
        kwargs: dict[str, object] = {
            "model": self.model,
            "input": list(texts),
        }
        if self.dimensions is not None:
            kwargs["dimensions"] = self.dimensions
        response = embed(**kwargs)
        return normalize_vectors(response.embeddings)
