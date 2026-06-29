from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ots import config
from ots.embedding_providers.base import EmbeddingProvider
from ots.embedding_providers.fastembed import FastEmbedEmbeddingProvider
from ots.embedding_providers.ollama import OllamaEmbeddingProvider
from ots.embedding_providers.openai import OpenAIEmbeddingProvider

REGISTERED_EMBEDDING_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    provider.provider_key: provider
    for provider in (
        FastEmbedEmbeddingProvider,
        OllamaEmbeddingProvider,
        OpenAIEmbeddingProvider,
    )
}

PROVIDER_OPTION_ALIASES: dict[str, dict[str, str]] = {
    "fastembed": {
        "fastembedCacheDir": "cache_dir",
        "fastembed_cache_dir": "cache_dir",
        "cacheDir": "cache_dir",
        "fastembedThreads": "threads",
        "fastembed_threads": "threads",
        "fastembedProviders": "providers",
        "fastembed_providers": "providers",
    },
    "ollama": {
        "ollamaHost": "host",
        "ollama_host": "host",
    },
    "openai": {
        "openaiApiKey": "api_key",
        "openai_api_key": "api_key",
        "openaiTimeout": "timeout",
        "openai_timeout": "timeout",
        "openaiMaxRetries": "max_retries",
        "openai_max_retries": "max_retries",
        "maxRetries": "max_retries",
    },
}


def normalize_provider_key(provider: str | None = None) -> str:
    provider_key = (provider or config.EMBEDDING_PROVIDER).strip().lower()
    if not provider_key:
        raise ValueError("embedding provider is required")
    return provider_key


def get_embedding_provider(provider: str | None = None) -> type[EmbeddingProvider]:
    provider_key = normalize_provider_key(provider)
    provider_class = REGISTERED_EMBEDDING_PROVIDERS.get(provider_key)
    if provider_class is None:
        supported = ", ".join(supported_providers())
        raise ValueError(
            f"Unsupported embedding provider: {provider_key}. Supported: {supported}"
        )
    return provider_class


def supported_providers() -> tuple[str, ...]:
    return tuple(sorted(REGISTERED_EMBEDDING_PROVIDERS))


def default_provider_model(provider: str | None = None) -> str:
    return get_embedding_provider(provider).default_model()


def default_dimensions(provider: str | None, model: str) -> int:
    return get_embedding_provider(provider).default_dimensions(model)


def normalize_provider_options(
    provider: str | None,
    provider_options: Mapping[str, Any] | None = None,
    *,
    include_defaults: bool = False,
) -> dict[str, Any]:
    provider_key = normalize_provider_key(provider)
    provider_class = get_embedding_provider(provider_key)
    aliases = PROVIDER_OPTION_ALIASES.get(provider_key, {})
    options: dict[str, Any] = (
        provider_class.default_options() if include_defaults else {}
    )
    for key, value in dict(provider_options or {}).items():
        normalized_key = aliases.get(key, key)
        options[normalized_key] = value
    return provider_class.normalize_options(options)


def default_provider_options(provider: str | None = None) -> dict[str, Any]:
    return normalize_provider_options(provider, include_defaults=True)


def create_embedder(
    *,
    provider: str,
    model: str,
    dimensions: int | None = None,
    provider_options: Mapping[str, Any] | None = None,
    **legacy_options: Any,
) -> EmbeddingProvider:
    provider_class = get_embedding_provider(provider)
    options = normalize_provider_options(
        provider, legacy_options, include_defaults=True
    )
    options.update(normalize_provider_options(provider, provider_options))
    return provider_class.from_options(
        model=model,
        dimensions=dimensions,
        options=options,
    )


def _hashable_option_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (str(key), _hashable_option_value(item)) for key, item in value.items()
            )
        )
    if isinstance(value, list | tuple | set):
        return tuple(_hashable_option_value(item) for item in value)
    return value


def provider_options_cache_key(
    provider: str,
    provider_options: Mapping[str, Any] | None = None,
) -> tuple[tuple[str, Any], ...]:
    options = normalize_provider_options(
        provider, provider_options, include_defaults=True
    )
    cache_items: list[tuple[str, Any]] = []
    for key, value in sorted(options.items()):
        cache_value = value
        if any(
            secret in key.lower() for secret in ("key", "token", "secret", "password")
        ):
            cache_value = (
                hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
                if value
                else None
            )
        cache_items.append((key, _hashable_option_value(cache_value)))
    return tuple(cache_items)
