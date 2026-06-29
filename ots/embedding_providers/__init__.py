from __future__ import annotations

from ots import config
from ots.embedding_providers.base import EmbeddingProvider
from ots.embedding_providers.fastembed import (
    DEFAULT_FASTEMBED_EMBEDDING_DIMENSIONS,
    DEFAULT_FASTEMBED_EMBEDDING_MODEL,
    FastEmbedEmbeddingProvider,
)
from ots.embedding_providers.ollama import (
    DEFAULT_OLLAMA_EMBEDDING_DIMENSIONS,
    DEFAULT_OLLAMA_EMBEDDING_MODEL,
    QWEN3_EMBEDDING_DIMENSIONS,
    QWEN3_EMBEDDING_MODEL,
    OllamaEmbeddingProvider,
)
from ots.embedding_providers.openai import (
    DEFAULT_OPENAI_EMBEDDING_DIMENSIONS,
    DEFAULT_OPENAI_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)
from ots.embedding_providers.registry import (
    REGISTERED_EMBEDDING_PROVIDERS,
    create_embedder,
    default_dimensions,
    default_provider_model,
    default_provider_options,
    get_embedding_provider,
    normalize_provider_key,
    normalize_provider_options,
    provider_options_cache_key,
    supported_providers,
)
from ots.embedding_providers.utils import normalize_vector, normalize_vectors

DEFAULT_EMBEDDING_PROVIDER = config.DEFAULT_EMBEDDING_PROVIDER
DEFAULT_EMBEDDING_MODEL = DEFAULT_OLLAMA_EMBEDDING_MODEL
DEFAULT_EMBEDDING_MODEL_KEY = f"{DEFAULT_EMBEDDING_PROVIDER}:{DEFAULT_EMBEDDING_MODEL}"
DEFAULT_QWEN3_EMBEDDING_MODEL = QWEN3_EMBEDDING_MODEL
DEFAULT_QWEN3_EMBEDDING_DIMENSIONS = QWEN3_EMBEDDING_DIMENSIONS
MAX_INDEXED_VECTOR_DIMENSIONS = 2000
MAX_INDEXED_HALFVEC_DIMENSIONS = 4000

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_MODEL_KEY",
    "DEFAULT_EMBEDDING_PROVIDER",
    "DEFAULT_FASTEMBED_EMBEDDING_DIMENSIONS",
    "DEFAULT_FASTEMBED_EMBEDDING_MODEL",
    "DEFAULT_OPENAI_EMBEDDING_DIMENSIONS",
    "DEFAULT_OPENAI_EMBEDDING_MODEL",
    "DEFAULT_QWEN3_EMBEDDING_DIMENSIONS",
    "DEFAULT_QWEN3_EMBEDDING_MODEL",
    "MAX_INDEXED_HALFVEC_DIMENSIONS",
    "MAX_INDEXED_VECTOR_DIMENSIONS",
    "REGISTERED_EMBEDDING_PROVIDERS",
    "EmbeddingProvider",
    "FastEmbedEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "create_embedder",
    "default_dimensions",
    "default_provider_model",
    "default_provider_options",
    "get_embedding_provider",
    "normalize_provider_key",
    "normalize_provider_options",
    "normalize_vector",
    "normalize_vectors",
    "provider_options_cache_key",
    "supported_providers",
]
