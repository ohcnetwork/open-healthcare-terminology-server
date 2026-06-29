from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from ots.embedding_providers.base import EmbeddingProvider
from ots.embedding_providers.utils import normalize_vectors

DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_OPENAI_EMBEDDING_DIMENSIONS = 3072
DEFAULT_OPENAI_TIMEOUT = 120.0
DEFAULT_OPENAI_MAX_RETRIES = 2


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


class OpenAIEmbeddingProvider(EmbeddingProvider):
    provider_key = "openai"

    @classmethod
    def default_model(cls) -> str:
        return DEFAULT_OPENAI_EMBEDDING_MODEL

    @classmethod
    def default_dimensions(cls, model: str) -> int:
        return DEFAULT_OPENAI_EMBEDDING_DIMENSIONS

    @classmethod
    def default_options(cls) -> dict[str, Any]:
        return {
            "api_key": os.getenv("OPENAI_API_KEY") or None,
            "timeout": _env_float("OTS_OPENAI_TIMEOUT", DEFAULT_OPENAI_TIMEOUT),
            "max_retries": _env_int(
                "OTS_OPENAI_MAX_RETRIES", DEFAULT_OPENAI_MAX_RETRIES
            ),
        }

    @classmethod
    def normalize_options(cls, options: dict[str, Any]) -> dict[str, Any]:
        allowed = {"api_key", "timeout", "max_retries"}
        return {
            key: value
            for key, value in options.items()
            if key in allowed and value is not None
        }

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        dimensions: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI embedding support requires the openai package. "
                "Run `pipenv install` to sync dependencies."
            ) from exc

        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout
        client_kwargs: dict[str, object] = {
            "api_key": api_key or os.getenv("OPENAI_API_KEY"),
        }
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self.client = OpenAI(**client_kwargs)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        params: dict[str, object] = {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            params["dimensions"] = self.dimensions
        client = (
            self.client.with_options(timeout=self.timeout)
            if self.timeout is not None
            else self.client
        )
        response = client.embeddings.create(**params)
        return normalize_vectors([item.embedding for item in response.data])
