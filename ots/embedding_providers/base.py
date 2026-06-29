from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


class EmbeddingProvider(ABC):
    """Base contract for embedding providers.

    Provider implementations should keep all provider-specific SDK behavior in
    their own module and return one vector per input text.
    """

    provider_key: str

    @classmethod
    def default_model(cls) -> str:
        raise NotImplementedError

    @classmethod
    def default_dimensions(cls, model: str) -> int:
        raise NotImplementedError

    @classmethod
    def default_options(cls) -> dict[str, Any]:
        return {}

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
    ) -> EmbeddingProvider:
        return cls(model=model, dimensions=dimensions, **(options or {}))

    @abstractmethod
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        pass

    def encode_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self.encode(texts)
