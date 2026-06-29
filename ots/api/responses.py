from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from starlette.responses import JSONResponse


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def json_response(payload: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(json_ready(payload), status_code=status_code)


def json_error(message: str, status_code: int = 400, **extra: Any) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    payload.update(extra)
    return json_response(payload, status_code=status_code)


def concept_payload_code(row: dict[str, Any]) -> None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return
    code = payload.get("code")
    if code is None:
        return
    row.setdefault("code", code)
    row.setdefault("display_code", payload.get("displayCode") or code)


def concept_response(row: dict[str, Any] | None) -> JSONResponse:
    if row is None:
        return json_error("Concept not found", status_code=404)
    concept_payload_code(row)
    return json_response(row)
