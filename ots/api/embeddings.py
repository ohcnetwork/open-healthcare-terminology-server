from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any

from ots.embedding_providers import (
    create_embedder,
    default_dimensions,
    default_provider_model,
    default_provider_options,
    provider_options_cache_key,
)
from ots import config
from ots.db.terminology_postgres import (
    get_embedding_model,
    vector_search_concepts,
)

_EMBEDDERS: dict[tuple[str, str, int | None, tuple[tuple[str, Any], ...]], object] = {}
_QUERY_EMBEDDING_CACHE: OrderedDict[
    tuple[str, str, str, int, str],
    tuple[float, ...],
] = OrderedDict()
_QUERY_EMBEDDING_CACHE_LOCK = RLock()


def embedding_provider() -> str:
    return config.EMBEDDING_PROVIDER


def embedding_model() -> str:
    return config.EMBEDDING_MODEL or default_provider_model(embedding_provider())


def embedding_model_key() -> str:
    return config.EMBEDDING_MODEL_KEY or f"{embedding_provider()}:{embedding_model()}"


def embedding_dimensions() -> int | None:
    return config.EMBEDDING_DIMENSIONS_OVERRIDE


def get_embedder(*, provider: str, model: str, dimensions: int | None):
    provider_options = default_provider_options(provider)
    cache_key = (
        provider,
        model,
        dimensions,
        provider_options_cache_key(provider, provider_options),
    )
    embedder = _EMBEDDERS.get(cache_key)
    if embedder is None:
        embedder = create_embedder(
            provider=provider,
            model=model,
            dimensions=dimensions,
            provider_options=provider_options,
        )
        _EMBEDDERS[cache_key] = embedder
    return embedder


def encode_query_embedding(
    *,
    query: str,
    provider: str,
    provider_model: str,
    model_key: str,
    dimensions: int,
) -> tuple[list[float], bool]:
    cache_size = 0 if config.DISABLE_QUERY_EMBEDDING_CACHE else max(
        config.QUERY_EMBEDDING_CACHE_SIZE,
        0,
    )
    if cache_size == 0:
        with _QUERY_EMBEDDING_CACHE_LOCK:
            _QUERY_EMBEDDING_CACHE.clear()
    cache_key = (provider, provider_model, model_key, dimensions, query)
    if cache_size > 0:
        with _QUERY_EMBEDDING_CACHE_LOCK:
            cached = _QUERY_EMBEDDING_CACHE.get(cache_key)
            if cached is not None:
                _QUERY_EMBEDDING_CACHE.move_to_end(cache_key)
                return list(cached), True

    embedder = get_embedder(
        provider=provider,
        model=provider_model,
        dimensions=dimensions,
    )
    embedding = embedder.encode_query([query])[0]

    if cache_size > 0:
        with _QUERY_EMBEDDING_CACHE_LOCK:
            _QUERY_EMBEDDING_CACHE[cache_key] = tuple(float(value) for value in embedding)
            _QUERY_EMBEDDING_CACHE.move_to_end(cache_key)
            while len(_QUERY_EMBEDDING_CACHE) > cache_size:
                _QUERY_EMBEDDING_CACHE.popitem(last=False)

    return embedding, False


def semantic_search_sync(
    *,
    query: str,
    raw_embedding: list[float] | None,
    terminology_key: str,
    terminology_version: str | None,
    model_key: str,
    provider_override: str | None,
    provider_model_override: str | None,
    dimensions_override: int | None,
    limit: int,
    ancestor_concept_id: int | None,
    include_ancestor: bool,
    active_only: bool,
    include_details: bool,
    include_query: bool,
    semantic_tags: list[str] | None,
    vector_search_strategy: str | None,
    response_mode: str = "vector",
) -> dict[str, Any]:
    model_config = get_embedding_model(
        model_key,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    if model_config is None:
        raise ValueError(f"Embedding model {model_key!r} has not been populated")
    provider = str(provider_override or model_config["provider"] or embedding_provider())
    provider_model = str(provider_model_override or model_config["provider_model"] or embedding_model())
    storage_type = str(model_config.get("storage_type") or "vector")
    dimensions = int(
        dimensions_override
        or model_config["dimensions"]
        or embedding_dimensions()
        or default_dimensions(provider, provider_model)
    )
    embedding_cache_hit: bool | None = None
    if raw_embedding is None:
        embedding, embedding_cache_hit = encode_query_embedding(
            query=query,
            provider=provider,
            provider_model=provider_model,
            model_key=model_key,
            dimensions=dimensions,
        )
    else:
        embedding = raw_embedding
    vector_result = vector_search_concepts(
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        model_key=model_key,
        embedding=embedding,
        limit=limit,
        ancestor_concept_id=ancestor_concept_id,
        include_ancestor=include_ancestor,
        active_only=active_only,
        include_details=include_details,
        include_query=include_query,
        semantic_tags=semantic_tags,
        vector_search_strategy=vector_search_strategy,
    )
    if include_query:
        rows, database_query = vector_result
    else:
        rows = vector_result
        database_query = None
    response = {
        "query": query or None,
        "terminology": terminology_key,
        "version": model_config.get("terminology_version") or terminology_version,
        "mode": response_mode,
        "ranking": "pgvector_cosine",
        "usesPostgresTextSearch": False,
        "modelKey": model_key,
        "provider": provider,
        "model": provider_model,
        "storageType": storage_type,
        "vectorSearchStrategy": vector_search_strategy or "halfvec_rerank"
        if storage_type == "halfvec"
        else None,
        "queryDimensions": len(embedding),
        "embeddingCacheHit": embedding_cache_hit,
        "limit": limit,
        "activeOnly": active_only,
        "detail": "full" if include_details else "basic",
        "scope": {
            "ancestorConceptId": ancestor_concept_id,
            "includeAncestor": include_ancestor,
        }
        if ancestor_concept_id is not None
        else None,
        "filters": {
            "semanticTags": semantic_tags,
        }
        if semantic_tags
        else None,
        "results": rows,
    }
    if response["vectorSearchStrategy"] is None:
        response.pop("vectorSearchStrategy")
    if database_query is not None:
        response["databaseQuery"] = database_query
    return response
