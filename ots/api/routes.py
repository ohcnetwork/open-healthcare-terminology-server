from __future__ import annotations

from starlette.routing import Route

from ots.api.openapi import openapi_endpoint, swagger_ui_endpoint
from ots.api.views.concepts import (
    children_endpoint,
    concept_endpoint,
    custom_record_collection_endpoint,
    custom_record_endpoint,
    descendants_endpoint,
    terminology_concept_endpoint,
    terminology_delete_endpoint,
)
from ots.api.views.fhir import (
    code_system_lookup_endpoint,
    terminology_lookup_endpoint,
    value_set_expand_endpoint,
)
from ots.api.views.jobs import embedding_job_endpoint, embedding_jobs_endpoint
from ots.api.views.search import search_get_endpoint, search_post_endpoint, semantic_search_endpoint
from ots.api.views.system import (
    embedding_status_endpoint,
    health,
    homepage,
    terminologies_endpoint,
)

routes = [
    Route("/docs", endpoint=swagger_ui_endpoint),
    Route("/openapi.json", endpoint=openapi_endpoint),
    Route("/", endpoint=homepage),
    Route("/health", endpoint=health),
    Route("/terminologies", endpoint=terminologies_endpoint, methods=["GET", "POST"]),
    Route(
        "/terminologies/{terminology:str}",
        endpoint=terminology_delete_endpoint,
        methods=["DELETE"],
    ),
    Route(
        "/terminologies/{terminology:str}/records",
        endpoint=custom_record_collection_endpoint,
        methods=["POST"],
    ),
    Route(
        "/terminologies/{terminology:str}/records/{code:str}",
        endpoint=custom_record_endpoint,
        methods=["PUT", "DELETE"],
    ),
    Route(
        "/terminologies/{terminology:str}/concepts/{code:str}",
        endpoint=terminology_concept_endpoint,
    ),
    Route(
        "/terminologies/{terminology:str}/lookup/{code:str}",
        endpoint=terminology_lookup_endpoint,
    ),
    Route("/ValueSet/$expand", endpoint=value_set_expand_endpoint, methods=["GET", "POST"]),
    Route("/CodeSystem/$lookup", endpoint=code_system_lookup_endpoint, methods=["GET", "POST"]),
    Route("/concepts/{concept_id:int}", endpoint=concept_endpoint),
    Route("/concepts/{concept_id:int}/children", endpoint=children_endpoint),
    Route("/concepts/{concept_id:int}/descendants", endpoint=descendants_endpoint),
    Route("/search", endpoint=search_get_endpoint, methods=["GET"]),
    Route("/search", endpoint=search_post_endpoint, methods=["POST"]),
    Route("/search/vector", endpoint=semantic_search_endpoint, methods=["POST"]),
    Route("/search/semantic", endpoint=semantic_search_endpoint, methods=["POST"]),
    Route("/embeddings/status", endpoint=embedding_status_endpoint),
    Route("/embeddings/jobs", endpoint=embedding_jobs_endpoint, methods=["GET", "POST"]),
    Route("/embeddings/jobs/{job_id:str}", endpoint=embedding_job_endpoint, methods=["GET"]),
]
