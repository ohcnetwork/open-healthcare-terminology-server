from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ots.api.embeddings import embedding_model_key, semantic_search_sync
from ots.api.parsers import (
    has_explicit_search_mode,
    parse_bool,
    parse_include_details,
    parse_include_query,
    parse_limit,
    parse_optional_int,
    parse_search_mode,
    parse_semantic_tags,
    parse_terminology,
    parse_terminology_version,
    parse_vector_search_strategy,
)
from ots.api.responses import json_error, json_response
from ots.db.terminology_postgres import search_concepts


def lexical_response(
    *,
    query: str,
    terminology_key: str,
    terminology_version: str | None,
    limit: int,
    active_only: bool,
    include_details: bool,
    ancestor_concept_id: int | None,
    include_ancestor: bool,
    semantic_tags: list[str] | None,
    lexical_result,
    include_query: bool,
) -> dict:
    if include_query:
        rows, database_query = lexical_result
    else:
        rows = lexical_result
        database_query = None
    response = {
        "query": query,
        "terminology": terminology_key,
        "version": terminology_version,
        "mode": "lexical",
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
    if database_query is not None:
        response["databaseQuery"] = database_query
    return response


async def search_get_endpoint(request: Request) -> JSONResponse:
    query = str(request.query_params.get("q", "")).strip()
    if not query:
        return json_error("Query parameter 'q' is required")
    try:
        limit = parse_limit(request.query_params.get("limit"), default=25, maximum=100)
        terminology_key = parse_terminology(request.query_params)
        ancestor_concept_id = parse_optional_int(
            request.query_params.get("ancestorConceptId")
            or request.query_params.get("parentConceptId"),
            field="ancestorConceptId",
        )
        include_ancestor = parse_bool(request.query_params.get("includeAncestor"), default=True)
        active_only = parse_bool(request.query_params.get("activeOnly"), default=True)
        include_details = parse_include_details(request.query_params, default=False)
        include_query = parse_include_query(request.query_params, default=False)
        terminology_version = parse_terminology_version(request.query_params)
        semantic_tags = parse_semantic_tags(request.query_params)
        vector_search_strategy = parse_vector_search_strategy(request.query_params)
        search_mode = parse_search_mode(request.query_params)
    except ValueError as exc:
        return json_error(str(exc))
    if search_mode == "vector":
        try:
            response = await run_in_threadpool(
                semantic_search_sync,
                query=query,
                raw_embedding=None,
                terminology_key=terminology_key,
                terminology_version=terminology_version,
                model_key=str(request.query_params.get("modelKey") or embedding_model_key()),
                provider_override=request.query_params.get("provider"),
                provider_model_override=request.query_params.get("model"),
                dimensions_override=(
                    int(request.query_params["dimensions"])
                    if request.query_params.get("dimensions")
                    else None
                ),
                limit=limit,
                ancestor_concept_id=ancestor_concept_id,
                include_ancestor=include_ancestor,
                active_only=active_only,
                include_details=include_details,
                include_query=include_query,
                semantic_tags=semantic_tags,
                vector_search_strategy=vector_search_strategy,
                response_mode="vector",
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=422)
        except Exception as exc:
            return json_error(f"Semantic search failed: {exc}", status_code=503)
        return json_response(response)
    lexical_result = await run_in_threadpool(
        search_concepts,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        query=query,
        limit=limit,
        ancestor_concept_id=ancestor_concept_id,
        include_ancestor=include_ancestor,
        active_only=active_only,
        include_details=include_details,
        include_query=include_query,
        semantic_tags=semantic_tags,
    )
    return json_response(
        lexical_response(
            query=query,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            limit=limit,
            active_only=active_only,
            include_details=include_details,
            ancestor_concept_id=ancestor_concept_id,
            include_ancestor=include_ancestor,
            semantic_tags=semantic_tags,
            lexical_result=lexical_result,
            include_query=include_query,
        )
    )


async def search_post_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_error("Request body must be JSON")
    query = str(payload.get("query", "")).strip()
    if not query:
        return json_error("Field 'query' is required")
    try:
        limit = parse_limit(payload.get("limit"), default=25, maximum=100)
        terminology_key = parse_terminology(payload)
        ancestor_concept_id = parse_optional_int(
            payload.get("ancestorConceptId", payload.get("parentConceptId")),
            field="ancestorConceptId",
        )
        include_ancestor = parse_bool(payload.get("includeAncestor"), default=True)
        active_only = parse_bool(payload.get("activeOnly"), default=True)
        include_details = parse_include_details(payload, default=False)
        include_query = parse_include_query(payload, default=False)
        terminology_version = parse_terminology_version(payload)
        semantic_tags = parse_semantic_tags(payload)
        vector_search_strategy = parse_vector_search_strategy(payload)
        search_mode = parse_search_mode(payload)
    except ValueError as exc:
        return json_error(str(exc))
    if search_mode == "vector":
        raw_embedding = payload.get("embedding")
        if raw_embedding is not None and (
            not isinstance(raw_embedding, list)
            or not all(isinstance(value, int | float) for value in raw_embedding)
        ):
            return json_error("Field 'embedding' must be a numeric array")
        try:
            response = await run_in_threadpool(
                semantic_search_sync,
                query=query,
                raw_embedding=(
                    [float(value) for value in raw_embedding]
                    if raw_embedding is not None
                    else None
                ),
                terminology_key=terminology_key,
                terminology_version=terminology_version,
                model_key=str(payload.get("modelKey") or embedding_model_key()),
                provider_override=payload.get("provider"),
                provider_model_override=payload.get("model"),
                dimensions_override=(
                    int(payload["dimensions"])
                    if payload.get("dimensions")
                    else None
                ),
                limit=limit,
                ancestor_concept_id=ancestor_concept_id,
                include_ancestor=include_ancestor,
                active_only=active_only,
                include_details=include_details,
                include_query=include_query,
                semantic_tags=semantic_tags,
                vector_search_strategy=vector_search_strategy,
                response_mode="vector",
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=422)
        except Exception as exc:
            return json_error(f"Semantic search failed: {exc}", status_code=503)
        return json_response(response)
    lexical_result = await run_in_threadpool(
        search_concepts,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        query=query,
        limit=limit,
        ancestor_concept_id=ancestor_concept_id,
        include_ancestor=include_ancestor,
        active_only=active_only,
        include_details=include_details,
        include_query=include_query,
        semantic_tags=semantic_tags,
    )
    return json_response(
        lexical_response(
            query=query,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            limit=limit,
            active_only=active_only,
            include_details=include_details,
            ancestor_concept_id=ancestor_concept_id,
            include_ancestor=include_ancestor,
            semantic_tags=semantic_tags,
            lexical_result=lexical_result,
            include_query=include_query,
        )
    )


async def semantic_search_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_error("Request body must be JSON")
    query = str(payload.get("query", "")).strip()
    raw_embedding = payload.get("embedding")
    if not query and raw_embedding is None:
        return json_error("Field 'query' or 'embedding' is required")
    if raw_embedding is not None and (
        not isinstance(raw_embedding, list)
        or not all(isinstance(value, int | float) for value in raw_embedding)
    ):
        return json_error("Field 'embedding' must be a numeric array")
    try:
        if has_explicit_search_mode(payload) and parse_search_mode(payload) != "vector":
            return json_error(
                "mode='lexical' is only valid on /search. Use /search with mode='lexical', "
                "or use mode='vector' on this endpoint.",
                status_code=422,
            )
        model_key = str(payload.get("modelKey") or embedding_model_key())
        terminology_key = parse_terminology(payload)
        terminology_version = parse_terminology_version(payload)
        limit = parse_limit(payload.get("limit"), default=25, maximum=100)
        ancestor_concept_id = parse_optional_int(
            payload.get("ancestorConceptId", payload.get("parentConceptId")),
            field="ancestorConceptId",
        )
        include_ancestor = parse_bool(payload.get("includeAncestor"), default=True)
        active_only = parse_bool(payload.get("activeOnly"), default=True)
        include_details = parse_include_details(payload, default=False)
        include_query = parse_include_query(payload, default=False)
        semantic_tags = parse_semantic_tags(payload)
        vector_search_strategy = parse_vector_search_strategy(payload)
        response = await run_in_threadpool(
            semantic_search_sync,
            query=query,
            raw_embedding=(
                [float(value) for value in raw_embedding]
                if raw_embedding is not None
                else None
            ),
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            model_key=model_key,
            provider_override=payload.get("provider"),
            provider_model_override=payload.get("model"),
            dimensions_override=(
                int(payload["dimensions"])
                if payload.get("dimensions")
                else None
            ),
            limit=limit,
            ancestor_concept_id=ancestor_concept_id,
            include_ancestor=include_ancestor,
            active_only=active_only,
            include_details=include_details,
            include_query=include_query,
            semantic_tags=semantic_tags,
            vector_search_strategy=vector_search_strategy,
            response_mode="vector",
        )
    except ValueError as exc:
        return json_error(str(exc), status_code=422)
    except Exception as exc:
        return json_error(f"Semantic search failed: {exc}", status_code=503)
    return json_response(response)
