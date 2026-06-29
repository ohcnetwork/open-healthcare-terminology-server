from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ots.api.parsers import parse_bool, parse_limit, parse_terminology, parse_terminology_version
from ots.api.responses import concept_response, json_error, json_response
from ots.api.schemas import CustomRecordUpsertRequest
from ots.db.terminology_postgres import (
    delete_custom_record,
    delete_custom_terminology,
    get_concept,
    get_concept_by_code,
    list_children,
    list_descendants,
    upsert_custom_record,
)


async def concept_endpoint(request: Request) -> JSONResponse:
    try:
        concept_id = int(request.path_params["concept_id"])
    except (KeyError, TypeError, ValueError):
        return json_error("conceptId must be a positive SCTID")
    terminology_key = parse_terminology(request.query_params)
    terminology_version = parse_terminology_version(request.query_params)
    row = await run_in_threadpool(
        get_concept,
        concept_id,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    return concept_response(row)


async def terminology_concept_endpoint(request: Request) -> JSONResponse:
    terminology_key = str(request.path_params["terminology"])
    terminology_version = parse_terminology_version(request.query_params)
    code = str(request.path_params["code"]).strip()
    if not code:
        return json_error("code is required")
    row = await run_in_threadpool(
        get_concept_by_code,
        code,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    if row is None:
        try:
            concept_id = int(code)
        except ValueError:
            concept_id = None
        if concept_id is not None:
            row = await run_in_threadpool(
                get_concept,
                concept_id,
                terminology_key=terminology_key,
                terminology_version=terminology_version,
            )
    return concept_response(row)


async def terminology_delete_endpoint(request: Request) -> JSONResponse:
    terminology_key = str(request.path_params["terminology"])
    try:
        deleted = await run_in_threadpool(
            delete_custom_terminology,
            terminology_key=terminology_key,
        )
    except ValueError as exc:
        return json_error(str(exc), status_code=422)
    except Exception as exc:
        return json_error(f"Could not delete terminology: {exc}", status_code=503)
    if not deleted:
        return json_error("Terminology not found", status_code=404)
    return json_response({"terminology": terminology_key, "deleted": True})


def custom_record_args(
    *,
    terminology_key: str,
    payload: dict[str, Any],
    code_override: str | None = None,
) -> dict[str, Any]:
    model = CustomRecordUpsertRequest.model_validate(payload)
    return model.storage_args(terminology_key=terminology_key, code_override=code_override)


async def custom_record_collection_endpoint(request: Request) -> JSONResponse:
    terminology_key = str(request.path_params["terminology"])
    try:
        payload = await request.json()
    except Exception:
        return json_error("Request body must be JSON")
    try:
        row = await run_in_threadpool(
            upsert_custom_record,
            **custom_record_args(terminology_key=terminology_key, payload=payload),
        )
    except ValidationError as exc:
        return json_error("Invalid record payload", status_code=422, details=exc.errors())
    except ValueError as exc:
        return json_error(str(exc), status_code=422)
    except Exception as exc:
        return json_error(f"Could not upsert record: {exc}", status_code=503)
    return json_response(row)


async def custom_record_endpoint(request: Request) -> JSONResponse:
    terminology_key = str(request.path_params["terminology"])
    code = str(request.path_params["code"]).strip()
    if request.method == "DELETE":
        try:
            deleted = await run_in_threadpool(
                delete_custom_record,
                terminology_key=terminology_key,
                code=code,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=422)
        except Exception as exc:
            return json_error(f"Could not delete record: {exc}", status_code=503)
        if not deleted:
            return json_error("Record not found", status_code=404)
        return json_response({"terminology": terminology_key, "code": code, "deleted": True})
    try:
        payload = await request.json()
    except Exception:
        return json_error("Request body must be JSON")
    try:
        row = await run_in_threadpool(
            upsert_custom_record,
            **custom_record_args(
                terminology_key=terminology_key,
                payload=payload,
                code_override=code,
            ),
        )
    except ValidationError as exc:
        return json_error("Invalid record payload", status_code=422, details=exc.errors())
    except ValueError as exc:
        return json_error(str(exc), status_code=422)
    except Exception as exc:
        return json_error(f"Could not upsert record: {exc}", status_code=503)
    return json_response(row)


async def children_endpoint(request: Request) -> JSONResponse:
    try:
        concept_id = int(request.path_params["concept_id"])
        terminology_key = parse_terminology(request.query_params)
        terminology_version = parse_terminology_version(request.query_params)
        limit = parse_limit(request.query_params.get("limit"), default=100, maximum=500)
        active_only = parse_bool(request.query_params.get("activeOnly"), default=True)
    except ValueError as exc:
        return json_error(str(exc))
    rows = await run_in_threadpool(
        list_children,
        concept_id,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        limit=limit,
        active_only=active_only,
    )
    return json_response(
        {
            "conceptId": concept_id,
            "terminology": terminology_key,
            "version": terminology_version,
            "limit": limit,
            "activeOnly": active_only,
            "items": rows,
        }
    )


async def descendants_endpoint(request: Request) -> JSONResponse:
    try:
        concept_id = int(request.path_params["concept_id"])
        terminology_key = parse_terminology(request.query_params)
        terminology_version = parse_terminology_version(request.query_params)
        limit = parse_limit(request.query_params.get("limit"), default=100, maximum=1_000)
        include_self = parse_bool(request.query_params.get("includeSelf"), default=False)
        active_only = parse_bool(request.query_params.get("activeOnly"), default=True)
    except ValueError as exc:
        return json_error(str(exc))
    rows = await run_in_threadpool(
        list_descendants,
        concept_id,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        limit=limit,
        include_self=include_self,
        active_only=active_only,
    )
    return json_response(
        {
            "conceptId": concept_id,
            "terminology": terminology_key,
            "version": terminology_version,
            "limit": limit,
            "includeSelf": include_self,
            "activeOnly": active_only,
            "items": rows,
        }
    )
