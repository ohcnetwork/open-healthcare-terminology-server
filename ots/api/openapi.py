from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from ots.api.schemas import (
    CustomRecordUpsertRequest,
    EmbeddingPopulateRequest,
    TerminologyCreateRequest,
)
from ots import config


def _json_schema(model) -> dict[str, Any]:
    return model.model_json_schema(ref_template="#/components/schemas/{model}")


def openapi_schema() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Open Terminology Server",
            "version": "0.1.0",
            "description": (
                "Warning: this terminology server is in testing mode and is not "
                "intended for production use or clinical decision-making without "
                "independent validation, operational hardening, and governance review.\n\n"
                "Terminology search, custom terminology management, and "
                "embedding-backed semantic search."
            ),
        },
        "security": [{"ApiKeyAuth": []}],
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": config.API_KEY_HEADER,
                }
            },
            "schemas": {
                "TerminologyCreateRequest": _json_schema(TerminologyCreateRequest),
                "CustomRecordUpsertRequest": _json_schema(CustomRecordUpsertRequest),
                "EmbeddingPopulateRequest": _json_schema(EmbeddingPopulateRequest),
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                },
            },
        },
        "paths": {
            "/": {
                "get": {
                    "summary": "API summary",
                    "tags": ["System"],
                    "responses": {"200": {"description": "API metadata"}},
                }
            },
            "/health": {
                "get": {
                    "summary": "Database health",
                    "tags": ["System"],
                    "parameters": [_query_param("terminology", "string", False)],
                    "responses": {"200": {"description": "Database status"}},
                }
            },
            "/terminologies": {
                "get": {
                    "summary": "List terminologies",
                    "tags": ["Terminologies"],
                    "responses": {"200": {"description": "Terminology list"}},
                },
                "post": {
                    "summary": "Create a custom terminology",
                    "tags": ["Terminologies"],
                    "requestBody": _json_body("TerminologyCreateRequest"),
                    "responses": {
                        "201": {"description": "Created terminology"},
                        "422": {"description": "Validation error"},
                    },
                },
            },
            "/terminologies/{terminology}": {
                "delete": {
                    "summary": "Delete a custom terminology",
                    "tags": ["Terminologies"],
                    "parameters": [_path_param("terminology", "string")],
                    "responses": {"200": {"description": "Deleted terminology"}},
                }
            },
            "/terminologies/{terminology}/records": {
                "post": {
                    "summary": "Create or update a custom record",
                    "tags": ["Custom Records"],
                    "parameters": [_path_param("terminology", "string")],
                    "requestBody": _json_body("CustomRecordUpsertRequest"),
                    "responses": {"200": {"description": "Upserted record"}},
                }
            },
            "/terminologies/{terminology}/records/{code}": {
                "put": {
                    "summary": "Create or update a custom record by code",
                    "tags": ["Custom Records"],
                    "parameters": [
                        _path_param("terminology", "string"),
                        _path_param("code", "string"),
                    ],
                    "requestBody": _json_body("CustomRecordUpsertRequest"),
                    "responses": {"200": {"description": "Upserted record"}},
                },
                "delete": {
                    "summary": "Delete a custom record",
                    "tags": ["Custom Records"],
                    "parameters": [
                        _path_param("terminology", "string"),
                        _path_param("code", "string"),
                    ],
                    "responses": {"200": {"description": "Deleted record"}},
                },
            },
            "/terminologies/{terminology}/concepts/{code}": {
                "get": {
                    "summary": "Get concept by code",
                    "tags": ["Concepts"],
                    "parameters": [
                        _path_param("terminology", "string"),
                        _path_param("code", "string"),
                    ],
                    "responses": {"200": {"description": "Concept document"}},
                }
            },
            "/terminologies/{terminology}/lookup/{code}": {
                "get": {
                    "summary": "FHIR-style code lookup by terminology",
                    "tags": ["FHIR"],
                    "parameters": [
                        _path_param("terminology", "string"),
                        _path_param("code", "string"),
                    ],
                    "responses": {"200": {"description": "FHIR Parameters lookup response"}},
                }
            },
            "/ValueSet/$expand": {
                "post": {
                    "summary": "FHIR ValueSet expand",
                    "description": "Supports ValueSet.compose.include rules with concept is-a filters and semanticTag filters. Text filters try vector search with the default embedding model and fall back to lexical filtering if embedding fails.",
                    "tags": ["FHIR"],
                    "parameters": [
                        _query_param("filter", "string", False),
                        _query_param("count", "integer", False),
                        _query_param("offset", "integer", False),
                        _query_param("version", "string", False),
                        _query_param("activeOnly", "boolean", False),
                        _query_param("includeDesignations", "boolean", False),
                        _query_param("displayLanguage", "string", False),
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/fhir+json": {"schema": {"type": "object"}},
                            "application/json": {"schema": {"type": "object"}},
                        },
                    },
                    "responses": {"200": {"description": "CARE results plus FHIR ValueSet expansion"}},
                }
            },
            "/CodeSystem/$lookup": {
                "get": {
                    "summary": "FHIR CodeSystem lookup",
                    "tags": ["FHIR"],
                    "parameters": [
                        _query_param("system", "string", True),
                        _query_param("version", "string", False),
                        _query_param("code", "string", True),
                    ],
                    "responses": {"200": {"description": "FHIR Parameters lookup response"}},
                },
                "post": {
                    "summary": "FHIR CodeSystem lookup with Parameters",
                    "tags": ["FHIR"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/fhir+json": {"schema": {"type": "object"}},
                            "application/json": {"schema": {"type": "object"}},
                        },
                    },
                    "responses": {"200": {"description": "FHIR Parameters lookup response"}},
                },
            },
            "/concepts/{concept_id}": {
                "get": {
                    "summary": "Get concept by internal concept id",
                    "tags": ["Concepts"],
                    "parameters": [
                        _path_param("concept_id", "integer"),
                        _query_param("terminology", "string", False),
                    ],
                    "responses": {"200": {"description": "Concept document"}},
                }
            },
            "/concepts/{concept_id}/children": {
                "get": {
                    "summary": "List direct children",
                    "tags": ["Concepts"],
                    "parameters": _concept_scope_params(),
                    "responses": {"200": {"description": "Children"}},
                }
            },
            "/concepts/{concept_id}/descendants": {
                "get": {
                    "summary": "List descendants",
                    "tags": ["Concepts"],
                    "parameters": [
                        *_concept_scope_params(),
                        _query_param("includeSelf", "boolean", False),
                    ],
                    "responses": {"200": {"description": "Descendants"}},
                }
            },
            "/search": {
                "get": {
                    "summary": "Search concepts",
                    "tags": ["Search"],
                    "parameters": [
                        _query_param("q", "string", True),
                        _query_param("terminology", "string", False),
                        _query_param("mode", "string", False, enum=["lexical", "vector"]),
                        _query_param("limit", "integer", False),
                        _query_param("detail", "string", False, enum=["basic", "full"]),
                        _query_param("semanticTag", "string", False),
                    ],
                    "responses": {"200": {"description": "Search results"}},
                },
                "post": {
                    "summary": "Search concepts with JSON request",
                    "tags": ["Search"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["query"],
                                    "properties": _search_properties(),
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Search results"}},
                },
            },
            "/search/semantic": _semantic_path_item(),
            "/search/vector": _semantic_path_item(),
            "/embeddings/status": {
                "get": {
                    "summary": "Embedding population status",
                    "tags": ["Embeddings"],
                    "parameters": [_query_param("terminology", "string", False)],
                    "responses": {"200": {"description": "Embedding status"}},
                }
            },
            "/embeddings/jobs": {
                "get": {
                    "summary": "Embedding job configuration",
                    "tags": ["Embeddings"],
                    "responses": {"200": {"description": "Celery embedding job configuration"}},
                },
                "post": {
                    "summary": "Start background embedding population",
                    "tags": ["Embeddings"],
                    "requestBody": _json_body("EmbeddingPopulateRequest"),
                    "responses": {
                        "202": {"description": "Embedding job enqueued"},
                        "422": {"description": "Validation error"},
                    },
                },
            },
            "/embeddings/jobs/{job_id}": {
                "get": {
                    "summary": "Get embedding job status",
                    "tags": ["Embeddings"],
                    "parameters": [_path_param("job_id", "string")],
                    "responses": {"200": {"description": "Embedding job state"}},
                }
            },
        },
    }


def _path_param(name: str, schema_type: str) -> dict[str, Any]:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": schema_type},
    }


def _query_param(
    name: str,
    schema_type: str,
    required: bool,
    *,
    enum: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": schema_type}
    if enum:
        schema["enum"] = enum
    return {"name": name, "in": "query", "required": required, "schema": schema}


def _json_body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"}
            }
        },
    }


def _concept_scope_params() -> list[dict[str, Any]]:
    return [
        _path_param("concept_id", "integer"),
        _query_param("terminology", "string", False),
        _query_param("limit", "integer", False),
        _query_param("activeOnly", "boolean", False),
    ]


def _search_properties() -> dict[str, Any]:
    return {
        "query": {"type": "string"},
        "terminology": {"type": "string"},
        "mode": {"type": "string", "enum": ["lexical", "vector"]},
        "limit": {"type": "integer", "default": 25},
        "detail": {"type": "string", "enum": ["basic", "full"]},
        "includeDetails": {"type": "boolean"},
        "showQuery": {"type": "boolean"},
        "activeOnly": {"type": "boolean"},
        "ancestorConceptId": {"type": "integer"},
        "parentConceptId": {"type": "integer"},
        "includeAncestor": {"type": "boolean"},
        "semanticTag": {"type": "string"},
        "semanticTags": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "modelKey": {"type": "string"},
        "provider": {"type": "string"},
        "model": {"type": "string"},
        "dimensions": {"type": "integer"},
        "embedding": {"type": "array", "items": {"type": "number"}},
        "vectorSearchStrategy": {
            "type": "string",
            "enum": ["halfvec_rerank", "full_exact", "halfvec_only"],
        },
    }


def _semantic_path_item() -> dict[str, Any]:
    return {
        "post": {
            "summary": "Vector semantic search",
            "tags": ["Search"],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": _search_properties(),
                            "anyOf": [
                                {"required": ["query"]},
                                {"required": ["embedding"]},
                            ],
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Vector search results",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "embeddingCacheHit": {
                                        "type": ["boolean", "null"],
                                        "description": "Whether the text query embedding came from the in-process model cache. Null for raw embedding requests.",
                                    }
                                },
                            }
                        }
                    },
                }
            },
        }
    }


async def openapi_endpoint(request: Request) -> JSONResponse:
    return JSONResponse(openapi_schema())


async def swagger_ui_endpoint(request: Request) -> HTMLResponse:
    return HTMLResponse(
        """
        <!doctype html>
        <html>
          <head>
            <title>Open Terminology Server API</title>
            <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
          </head>
          <body>
            <div id="swagger-ui"></div>
            <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
            <script>
              window.ui = SwaggerUIBundle({
                url: "/openapi.json",
                dom_id: "#swagger-ui",
                deepLinking: true,
                persistAuthorization: true
              });
            </script>
          </body>
        </html>
        """
    )
