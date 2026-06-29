from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from ots.embedding_providers.base import EmbeddingProvider
from ots.embedding_providers.utils import normalize_vectors

DEFAULT_FASTEMBED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_FASTEMBED_EMBEDDING_DIMENSIONS = 384


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else None


def _env_csv(name: str) -> list[str]:
    return [item.strip() for item in (os.getenv(name) or "").split(",") if item.strip()]


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    provider_key = "fastembed"

    @classmethod
    def default_model(cls) -> str:
        return DEFAULT_FASTEMBED_EMBEDDING_MODEL

    @classmethod
    def default_dimensions(cls, model: str) -> int:
        return DEFAULT_FASTEMBED_EMBEDDING_DIMENSIONS

    @classmethod
    def default_options(cls) -> dict[str, Any]:
        return {
            "cache_dir": os.getenv("OTS_FASTEMBED_CACHE_DIR") or None,
            "threads": _env_int("OTS_FASTEMBED_THREADS"),
            "providers": _env_csv("OTS_FASTEMBED_PROVIDERS"),
        }

    @classmethod
    def normalize_options(cls, options: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in options.items() if value is not None}

    @classmethod
    def from_options(
        cls,
        *,
        model: str,
        dimensions: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> FastEmbedEmbeddingProvider:
        return cls(model=model, **(options or {}))

    def __init__(
        self,
        *,
        model: str,
        cache_dir: str | None = None,
        threads: int | None = None,
        providers: list[str] | None = None,
        **kwargs,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "FastEmbed support requires the fastembed package. "
                "Run `pipenv install` to sync dependencies."
            ) from exc

        init_kwargs: dict[str, object] = {"model_name": model}
        if cache_dir is not None:
            init_kwargs["cache_dir"] = cache_dir
        if threads is not None:
            init_kwargs["threads"] = threads
        if providers:
            init_kwargs["providers"] = providers
        init_kwargs.update(kwargs)
        self.model = model
        self.embedding_model = TextEmbedding(**init_kwargs)

    @staticmethod
    def _materialize(vectors) -> list[list[float]]:
        return normalize_vectors(
            vector.tolist() if hasattr(vector, "tolist") else vector
            for vector in vectors
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._materialize(self.embedding_model.passage_embed(list(texts)))

    def encode_query(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._materialize(self.embedding_model.query_embed(list(texts)))
