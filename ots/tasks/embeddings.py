from __future__ import annotations

from argparse import Namespace
from typing import Any

from celery import states

from ots import config
from ots.embedding_providers import (
    default_dimensions,
    default_provider_model,
)
from ots.worker import celery_app
from ots.cli.common.update_concept_embeddings import run_embedding_update


def _clean_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _clean_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _clean_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clean_csv(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def _embedding_provider_options(payload: dict[str, Any]) -> dict[str, Any]:
    legacy_options = {
        "ollamaHost": payload.get("ollamaHost"),
        "openaiTimeout": payload.get("openaiTimeout"),
        "openaiMaxRetries": payload.get("openaiMaxRetries"),
        "fastembedCacheDir": payload.get("fastembedCacheDir"),
        "fastembedThreads": payload.get("fastembedThreads"),
        "fastembedProviders": payload.get("fastembedProviders"),
    }
    provider_options = payload.get("providerOptions") or payload.get("provider_options") or {}
    if not isinstance(provider_options, dict):
        raise ValueError("providerOptions must be a JSON object")
    merged = {key: value for key, value in legacy_options.items() if value is not None}
    merged.update(provider_options)
    return merged


def embedding_namespace(payload: dict[str, Any]) -> Namespace:
    provider = str(payload.get("provider") or config.EMBEDDING_PROVIDER)
    model = str(payload.get("model") or default_provider_model(provider))
    dimensions = _clean_int(payload.get("dimensions"))
    if dimensions is None:
        dimensions = default_dimensions(provider, model)
    return Namespace(
        database_url=str(payload.get("databaseUrl") or config.DATABASE_URL),
        terminology=str(payload.get("terminology") or config.TERMINOLOGY_KEY),
        version=payload.get("version") or payload.get("terminologyVersion"),
        provider=provider,
        model=model,
        model_key=payload.get("modelKey"),
        dimensions=dimensions,
        storage_type=str(payload.get("storageType") or "auto"),
        batch_size=int(payload.get("batchSize") or 64),
        parallel_requests=int(
            payload.get("parallelRequests") or config.EMBEDDING_PARALLEL_REQUESTS
        ),
        limit=_clean_int(payload.get("limit")),
        after_concept_id=_clean_int(payload.get("afterConceptId")),
        refresh=_clean_bool(payload.get("refresh")),
        include_inactive=_clean_bool(payload.get("includeInactive")),
        semantic_tags=_clean_csv(payload.get("semanticTags")),
        all_semantic_tags=_clean_bool(payload.get("allSemanticTags")),
        provider_options=_embedding_provider_options(payload),
        max_input_chars=_clean_int(payload.get("maxInputChars")),
        skip_index=_clean_bool(payload.get("skipIndex")),
        recreate_index=_clean_bool(payload.get("recreateIndex")),
    )


@celery_app.task(bind=True, name="ots.embeddings.populate")
def populate_embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
    args = embedding_namespace(payload)

    def progress(meta: dict[str, Any]) -> None:
        self.update_state(state="PROGRESS", meta=meta)

    try:
        result = run_embedding_update(args, progress_callback=progress)
    except Exception as exc:
        self.update_state(
            state=states.FAILURE,
            meta={"error": str(exc), "type": type(exc).__name__},
        )
        raise
    return result
