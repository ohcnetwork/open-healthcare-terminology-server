#!/usr/bin/env python3
"""Populate concept-level embeddings for a model key."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ots import config
from ots.db.terminology_postgres import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_TERMINOLOGY_KEY,
    MAX_VECTOR_INDEX_DIMENSIONS,
    connect_db,
    count_embedding_inputs,
    default_model_key,
    drop_embedding_index,
    ensure_embedding_index,
    init_schema,
    iter_embedding_inputs,
    register_embedding_model,
    resolve_embedding_storage_type,
    upsert_concept_embeddings,
)
from ots.embedding_providers import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
    create_embedder,
    default_dimensions,
    default_provider_model,
    normalize_provider_options,
    supported_providers,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL",
    )
    parser.add_argument(
        "--terminology",
        default=config.TERMINOLOGY_KEY,
        help=f"Terminology key/table namespace (default: {DEFAULT_TERMINOLOGY_KEY})",
    )
    parser.add_argument(
        "--version",
        help="Terminology version to embed. Defaults to the terminology's default version.",
    )
    parser.add_argument(
        "--provider",
        default=config.EMBEDDING_PROVIDER,
        choices=supported_providers(),
        help=f"Embedding provider (default: {DEFAULT_EMBEDDING_PROVIDER})",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Provider model name. Defaults to provider registry value "
            f"(default provider model: {DEFAULT_EMBEDDING_MODEL})."
        ),
    )
    parser.add_argument(
        "--model-key",
        default=None,
        help=(
            "Logical model key stored in Postgres. Defaults to a key derived from "
            "provider/model/dimensions unless OTS_EMBEDDING_MODEL_KEY is set."
        ),
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=config.EMBEDDING_DIMENSIONS_OVERRIDE,
        help="Embedding dimensions. Defaults depend on provider/model.",
    )
    parser.add_argument(
        "--provider-options",
        default=None,
        help=(
            "Provider-specific JSON object, or @path/to/options.json. "
            'Examples: \'{"host":"http://127.0.0.1:11434"}\', '
            '\'{"timeout":120,"max_retries":2}\', '
            '\'{"cache_dir":"data/models/fastembed","threads":8}\'.'
        ),
    )
    parser.add_argument(
        "--storage-type",
        choices=("auto", "vector", "halfvec"),
        default="auto",
        help=(
            "Embedding storage strategy. auto uses vector up to 2000 dimensions "
            "and vector+halfvec above that. Explicit vector can store larger "
            "unindexed embeddings when --skip-index is used."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Concepts to embed per provider request",
    )
    parser.add_argument(
        "--parallel-requests",
        type=int,
        default=config.EMBEDDING_PARALLEL_REQUESTS,
        help=(
            "Maximum embedding provider requests to run concurrently. "
            "Database writes remain serialized."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum concepts to embed in this run",
    )
    parser.add_argument(
        "--after-concept-id",
        type=int,
        help="Only embed concepts with IDs greater than this value",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recompute concepts even when this model key already has embeddings",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Embed inactive concepts too",
    )
    parser.add_argument(
        "--semantic-tags",
        default=None,
        help=(
            "Comma-separated semantic tags to embed. Default: disorder,finding for SNOMED, "
            "all tags for other terminologies. "
            "Use --all-semantic-tags to disable this filter."
        ),
    )
    parser.add_argument(
        "--all-semantic-tags",
        action="store_true",
        help="Embed concepts from every semantic tag instead of only disorders/findings.",
    )
    parser.add_argument(
        "--max-input-chars",
        type=int,
        help="Optional maximum characters per concept text sent to the embedding provider",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Skip creating/updating the model-specific HNSW index at the end",
    )
    parser.add_argument(
        "--recreate-index",
        action="store_true",
        help="Drop this model's HNSW index before embedding and rebuild it at the end",
    )
    return parser.parse_args()


def parse_provider_options(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    text = str(value).strip()
    if not text:
        return {}
    if text.startswith("@"):
        path = Path(text[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(
                f"Could not read provider options file {path}: {exc}"
            ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--provider-options must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--provider-options must decode to a JSON object")
    return parsed


def batched(items: Iterable[dict], batch_size: int) -> Iterable[list[dict]]:
    batch: list[dict] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_semantic_tags(value: str | None, *, include_all: bool) -> list[str] | None:
    if include_all:
        return None
    tags = [item.strip() for item in (value or "").split(",") if item.strip()]
    return tags or None


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s"


def build_embedder(args: argparse.Namespace):
    return create_embedder(
        provider=args.provider,
        model=args.model,
        dimensions=args.dimensions,
        provider_options=args.provider_options,
    )


def prepare_embedding_texts(
    rows: list[dict[str, Any]],
    *,
    max_input_chars: int | None,
) -> list[str]:
    texts = [row["search_text"] for row in rows]
    if max_input_chars is None:
        return texts
    if max_input_chars < 1:
        raise SystemExit("--max-input-chars must be greater than 0")
    return [text[:max_input_chars] for text in texts]


def embed_batch_worker(
    *,
    args: argparse.Namespace,
    thread_state: threading.local,
    batch_number: int,
    start_position: int,
    rows: list[dict[str, Any]],
    texts: list[str],
) -> dict[str, Any]:
    embedder = getattr(thread_state, "embedder", None)
    if embedder is None:
        embedder = build_embedder(args)
        thread_state.embedder = embedder
    provider_start = time.perf_counter()
    vectors = embedder.encode(texts)
    provider_elapsed = max(time.perf_counter() - provider_start, 1e-9)
    return {
        "batch_number": batch_number,
        "start_position": start_position,
        "rows": rows,
        "texts": texts,
        "vectors": vectors,
        "source_hashes": [source_hash(text) for text in texts],
        "provider_elapsed": provider_elapsed,
        "input_chars": sum(len(text) for text in texts),
    }


EmbeddingProgressCallback = Callable[[dict[str, Any]], None]


def run_embedding_update(
    args: argparse.Namespace,
    *,
    progress_callback: EmbeddingProgressCallback | None = None,
) -> dict[str, Any]:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be greater than 0")
    if args.parallel_requests < 1:
        raise SystemExit("--parallel-requests must be greater than 0")
    args.provider_options = normalize_provider_options(
        args.provider,
        parse_provider_options(getattr(args, "provider_options", None)),
        include_defaults=True,
    )
    args.model = args.model or default_provider_model(args.provider)
    args.dimensions = args.dimensions or default_dimensions(args.provider, args.model)
    if args.dimensions < 1:
        raise SystemExit("--dimensions must be greater than 0")
    requested_storage_type = args.storage_type
    if (
        requested_storage_type == "auto"
        and args.skip_index
        and args.dimensions > MAX_VECTOR_INDEX_DIMENSIONS
    ):
        requested_storage_type = "vector"
    try:
        storage_type = resolve_embedding_storage_type(
            dimensions=args.dimensions,
            requested_storage_type=requested_storage_type,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if (
        storage_type == "vector"
        and args.dimensions > MAX_VECTOR_INDEX_DIMENSIONS
        and not args.skip_index
    ):
        raise SystemExit(
            f"vector HNSW indexing supports up to {MAX_VECTOR_INDEX_DIMENSIONS} dimensions; "
            f"pass --skip-index to store unindexed vector({args.dimensions}), "
            "or use --storage-type halfvec for indexed high-dimensional search."
        )

    configured_model_key = (
        config.EMBEDDING_MODEL_KEY if os.getenv("OTS_EMBEDDING_MODEL_KEY") else None
    )
    if args.model_key or configured_model_key:
        model_key = args.model_key or configured_model_key
    elif args.provider == "openai":
        model_key = f"{default_model_key(args.provider, args.model)}:{args.dimensions}"
        if storage_type == "halfvec":
            model_key = f"{model_key}:halfvec"
    else:
        model_key = default_model_key(args.provider, args.model)
        if args.dimensions != DEFAULT_EMBEDDING_DIMENSIONS:
            model_key = f"{model_key}:{args.dimensions}"
    config.set_database_url(args.database_url)
    terminology_key = args.terminology
    semantic_tag_filter = args.semantic_tags
    if (
        semantic_tag_filter is None
        and terminology_key.strip().lower() == DEFAULT_TERMINOLOGY_KEY
    ):
        semantic_tag_filter = "disorder,finding"
    semantic_tags = parse_semantic_tags(
        semantic_tag_filter,
        include_all=args.all_semantic_tags,
    )
    start = time.perf_counter()

    def emit_progress(**payload: Any) -> None:
        if progress_callback is not None:
            progress_callback(payload)

    with connect_db() as conn:
        init_schema(
            conn,
            embedding_dimensions=args.dimensions,
            terminology_key=terminology_key,
            terminology_version=args.version,
        )
        register_embedding_model(
            conn,
            terminology_key=terminology_key,
            terminology_version=args.version,
            model_key=model_key,
            provider=args.provider,
            provider_model=args.model,
            dimensions=args.dimensions,
            storage_type=storage_type,
            distance="cosine",
            text_source="search_text",
        )
        conn.commit()

        if args.recreate_index:
            print("Dropping model-specific HNSW index before embedding", flush=True)
            drop_embedding_index(
                conn,
                terminology_key=terminology_key,
                terminology_version=args.version,
                model_key=model_key,
                dimensions=args.dimensions,
            )
            conn.commit()

        pending = count_embedding_inputs(
            conn,
            terminology_key=terminology_key,
            terminology_version=args.version,
            model_key=model_key,
            limit=args.limit,
            refresh=args.refresh,
            active_only=not args.include_inactive,
            after_concept_id=args.after_concept_id,
            semantic_tags=semantic_tags,
        )
        print(
            f"Embedding model {model_key!r} using {args.provider}:{args.model} "
            f"({args.dimensions} dimensions, {storage_type} storage) for {terminology_key!r} "
            f"version {args.version or 'default'}",
            flush=True,
        )
        if semantic_tags:
            print(f"Semantic tags: {', '.join(semantic_tags)}", flush=True)
        else:
            print("Semantic tags: all", flush=True)
        print(f"Parallel provider requests: {args.parallel_requests}", flush=True)
        print(f"Pending concepts: {pending:,}", flush=True)
        emit_progress(
            state="STARTED",
            terminology=terminology_key,
            version=args.version,
            modelKey=model_key,
            provider=args.provider,
            model=args.model,
            dimensions=args.dimensions,
            storageType=storage_type,
            pending=pending,
            embedded=0,
        )
        if pending == 0:
            print("Nothing to embed.")
            if not args.skip_index:
                index_start = time.perf_counter()
                print("Ensuring model-specific HNSW index", flush=True)
                ensure_embedding_index(
                    conn,
                    terminology_key=terminology_key,
                    terminology_version=args.version,
                    model_key=model_key,
                    dimensions=args.dimensions,
                    storage_type=storage_type,
                    distance="cosine",
                )
                conn.commit()
                print(
                    f"Index ready in {format_duration(time.perf_counter() - index_start)}"
                )
            return {
                "terminology": terminology_key,
                "version": args.version,
                "modelKey": model_key,
                "provider": args.provider,
                "model": args.model,
                "dimensions": args.dimensions,
                "storageType": storage_type,
                "pending": pending,
                "embedded": 0,
                "elapsedSeconds": time.perf_counter() - start,
            }

        rows = iter_embedding_inputs(
            conn,
            terminology_key=terminology_key,
            terminology_version=args.version,
            model_key=model_key,
            limit=args.limit,
            refresh=args.refresh,
            active_only=not args.include_inactive,
            after_concept_id=args.after_concept_id,
            semantic_tags=semantic_tags,
        )

        total = 0
        submitted = 0
        thread_state = threading.local()
        batch_iter = enumerate(batched(rows, args.batch_size), start=1)
        in_flight: set[Future[dict[str, Any]]] = set()

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            nonlocal submitted
            try:
                batch_number, batch = next(batch_iter)
            except StopIteration:
                return False
            start_position = submitted + 1
            submitted += len(batch)
            texts = prepare_embedding_texts(
                batch,
                max_input_chars=args.max_input_chars,
            )
            input_chars = sum(len(text) for text in texts)
            print(
                f"Requesting embeddings batch {batch_number:,} for concepts "
                f"{start_position:,}-{submitted:,} of {pending:,} "
                f"({input_chars:,} chars, in-flight {len(in_flight) + 1}/{args.parallel_requests})",
                flush=True,
            )
            future = executor.submit(
                embed_batch_worker,
                args=args,
                thread_state=thread_state,
                batch_number=batch_number,
                start_position=start_position,
                rows=batch,
                texts=texts,
            )
            in_flight.add(future)
            return True

        with ThreadPoolExecutor(max_workers=args.parallel_requests) as executor:
            while len(in_flight) < args.parallel_requests and submit_next(executor):
                pass
            while in_flight:
                done, pending_futures = wait(in_flight, return_when=FIRST_COMPLETED)
                in_flight = set(pending_futures)
                for future in done:
                    result = future.result()
                    vectors = result["vectors"]
                    if vectors and len(vectors[0]) != args.dimensions:
                        raise RuntimeError(
                            f"Model returned {len(vectors[0])} dimensions, "
                            f"expected {args.dimensions}"
                        )
                    database_start = time.perf_counter()
                    upsert_concept_embeddings(
                        conn,
                        terminology_key=terminology_key,
                        terminology_version=args.version,
                        model_key=model_key,
                        dimensions=args.dimensions,
                        storage_type=storage_type,
                        rows=result["rows"],
                        vectors=vectors,
                        source_hashes=result["source_hashes"],
                    )
                    conn.commit()
                    database_elapsed = max(time.perf_counter() - database_start, 1e-9)
                    total += len(result["rows"])
                    elapsed = max(time.perf_counter() - start, 1e-9)
                    provider_elapsed = float(result["provider_elapsed"])
                    provider_rate = len(result["rows"]) / provider_elapsed
                    print(
                        f"Embedded {total:,}/{pending:,} concepts "
                        f"({total / elapsed:.1f}/s overall); "
                        f"batch {result['batch_number']:,}: provider={provider_elapsed:.1f}s "
                        f"({provider_rate:.1f}/s), db={database_elapsed:.1f}s; "
                        f"last concept={result['rows'][-1]['concept_id']}",
                        flush=True,
                    )
                    emit_progress(
                        state="PROGRESS",
                        terminology=terminology_key,
                        version=args.version,
                        modelKey=model_key,
                        provider=args.provider,
                        model=args.model,
                        dimensions=args.dimensions,
                        storageType=storage_type,
                        pending=pending,
                        embedded=total,
                        submitted=submitted,
                        batchNumber=result["batch_number"],
                        lastConceptId=result["rows"][-1]["concept_id"],
                        providerElapsedSeconds=provider_elapsed,
                        databaseElapsedSeconds=database_elapsed,
                        elapsedSeconds=elapsed,
                        overallRate=total / elapsed,
                    )
                    submit_next(executor)

        if not args.skip_index:
            index_start = time.perf_counter()
            print("Ensuring model-specific HNSW index", flush=True)
            ensure_embedding_index(
                conn,
                terminology_key=terminology_key,
                terminology_version=args.version,
                model_key=model_key,
                dimensions=args.dimensions,
                storage_type=storage_type,
                distance="cosine",
            )
            conn.commit()
            print(
                f"Index ready in {format_duration(time.perf_counter() - index_start)}"
            )

    elapsed = time.perf_counter() - start
    print(f"Done in {format_duration(elapsed)}")
    return {
        "terminology": terminology_key,
        "version": args.version,
        "modelKey": model_key,
        "provider": args.provider,
        "model": args.model,
        "dimensions": args.dimensions,
        "storageType": storage_type,
        "pending": pending,
        "embedded": total,
        "elapsedSeconds": elapsed,
    }


def main() -> int:
    args = parse_args()
    run_embedding_update(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
