from __future__ import annotations

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ots import config
from ots.api.embeddings import embedding_model, embedding_model_key, embedding_provider
from ots.api.parsers import parse_terminology, parse_terminology_version
from ots.api.responses import json_error, json_response
from ots.api.schemas import TerminologyCreateRequest
from ots.db.terminology_postgres import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    create_custom_terminology,
    database_status,
    database_url,
    embedding_status,
    list_terminologies,
)


def homepage(request: Request) -> JSONResponse:
    return json_response(
        {
            "name": "Open Terminology Server",
            "warning": (
                "This terminology server is in testing mode and is not intended "
                "for production use or clinical decision-making without independent "
                "validation, operational hardening, and governance review."
            ),
            "storage": "postgres",
            "vectorExtension": "pgvector",
            "embeddingDimensions": DEFAULT_EMBEDDING_DIMENSIONS,
            "defaultTerminology": config.TERMINOLOGY_KEY,
            "defaultEmbeddingProvider": embedding_provider(),
            "defaultEmbeddingModel": embedding_model(),
            "defaultEmbeddingModelKey": embedding_model_key(),
            "endpoints": {
                "health": "GET /health",
                "terminologies": "GET /terminologies",
                "createTerminology": "POST /terminologies",
                "deleteTerminology": "DELETE /terminologies/{terminology}",
                "upsertRecord": "POST /terminologies/{terminology}/records",
                "upsertRecordByCode": "PUT /terminologies/{terminology}/records/{code}",
                "deleteRecord": "DELETE /terminologies/{terminology}/records/{code}",
                "concept": "GET /concepts/{conceptId}",
                "terminologyConcept": "GET /terminologies/{terminology}/concepts/{code}",
                "terminologyLookup": "GET /terminologies/{terminology}/lookup/{code}",
                "fhirValueSetExpand": "POST /ValueSet/$expand",
                "fhirCodeSystemLookup": "GET /CodeSystem/$lookup?system={system}&code={code}",
                "children": "GET /concepts/{conceptId}/children",
                "descendants": "GET /concepts/{conceptId}/descendants",
                "search": "GET /search?q=heart",
                "searchPost": "POST /search",
                "semanticSearch": "POST /search/semantic",
                "embeddingStatus": "GET /embeddings/status",
                "embeddingJobs": "POST /embeddings/jobs",
                "embeddingJobStatus": "GET /embeddings/jobs/{jobId}",
            },
        }
    )


async def health(request: Request) -> JSONResponse:
    try:
        terminology_key = parse_terminology(request.query_params)
        terminology_version = parse_terminology_version(request.query_params)
        status = await run_in_threadpool(
            database_status,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
    except Exception as exc:
        return json_error(f"Database unavailable: {exc}", status_code=503)
    return json_response(status)


async def terminologies_endpoint(request: Request) -> JSONResponse:
    if request.method == "POST":
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be JSON")
        try:
            model = TerminologyCreateRequest.model_validate(payload)
            item = await run_in_threadpool(
                create_custom_terminology,
                terminology_key=model.key,
                name=model.name,
                description=model.description,
                metadata=model.metadata,
                keywords=model.keywords,
                connections=model.connections,
            )
        except ValidationError as exc:
            return json_error("Invalid terminology payload", status_code=422, details=exc.errors())
        except ValueError as exc:
            return json_error(str(exc), status_code=422)
        except Exception as exc:
            return json_error(f"Could not create terminology: {exc}", status_code=503)
        return json_response(item, status_code=201)
    try:
        items = await run_in_threadpool(list_terminologies)
    except Exception as exc:
        return json_error(f"Database unavailable: {exc}", status_code=503)
    return json_response({"items": items})


async def embedding_status_endpoint(request: Request) -> JSONResponse:
    try:
        terminology_key = parse_terminology(request.query_params)
        terminology_version = parse_terminology_version(request.query_params)
        status = await run_in_threadpool(
            embedding_status,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
    except Exception as exc:
        return json_error(f"Database unavailable: {exc}", status_code=503)
    return json_response(status)
