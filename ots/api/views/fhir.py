from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ots.api.embeddings import embedding_model_key, semantic_search_sync
from ots.api.parsers import parse_bool, parse_limit
from ots.api.responses import json_error, json_response
from ots.db.terminology_postgres import (
    expand_value_set_concepts,
    get_concept,
    get_concept_by_code,
)
from ots.terminology import IMPORTED_TERMINOLOGIES, normalize_terminology_key

SYSTEM_ALIASES = {
    "snomed": "snomed",
    "http://snomed.info/sct": "snomed",
    "loinc": "loinc",
    "http://loinc.org": "loinc",
    "icd10cm": "icd10cm",
    "http://hl7.org/fhir/sid/icd-10-cm": "icd10cm",
    "icd11": "icd11",
    "https://icd.who.int/browse11/l-m/en": "icd11",
}


def system_for_terminology(terminology_key: str) -> str:
    terminology = IMPORTED_TERMINOLOGIES.get(normalize_terminology_key(terminology_key))
    if terminology and terminology.code_system_uri:
        return terminology.code_system_uri
    return normalize_terminology_key(terminology_key)


def terminology_for_system(system: str | None) -> str | None:
    if not system:
        return None
    normalized = str(system).strip()
    if not normalized:
        return None
    return SYSTEM_ALIASES.get(normalized, SYSTEM_ALIASES.get(normalized.lower()))


def parameter_value(parameter: dict[str, Any]) -> Any:
    for key in (
        "valueString",
        "valueCode",
        "valueUri",
        "valueCanonical",
        "valueInteger",
        "valueBoolean",
    ):
        if key in parameter:
            return parameter[key]
    if "resource" in parameter:
        return parameter["resource"]
    return None


def parameters_to_dict(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for parameter in payload.get("parameter") or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if not name:
            continue
        value = parameter_value(parameter)
        if name in values:
            current = values[name]
            if isinstance(current, list):
                current.append(value)
            else:
                values[name] = [current, value]
        else:
            values[name] = value
    return values


async def request_json_or_empty(request: Request) -> dict[str, Any]:
    if request.method != "POST":
        return {}
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def value_set_from_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if payload.get("resourceType") == "Parameters":
        params = parameters_to_dict(payload)
        value_set = params.get("valueSet")
        if not isinstance(value_set, dict):
            value_set = {}
        return value_set, params
    if payload.get("resourceType") == "ValueSet" or "compose" in payload:
        return payload, {}
    return {}, {}


def value_set_includes(value_set: dict[str, Any]) -> list[dict[str, Any]]:
    compose = value_set.get("compose") or {}
    includes = compose.get("include") or []
    if not isinstance(includes, list):
        raise ValueError("ValueSet.compose.include must be an array")
    return [item for item in includes if isinstance(item, dict)]


def parse_intish(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def parse_expand_options(
    *,
    request: Request,
    params: dict[str, Any],
) -> tuple[str | None, int, int, bool, bool, str | None, str | None]:
    query_params = request.query_params
    text_filter = (
        query_params.get("filter")
        or query_params.get("query")
        or params.get("filter")
        or params.get("query")
    )
    count = parse_limit(
        query_params.get("count") or params.get("count"),
        default=50,
        maximum=1_000,
    )
    offset = parse_intish(query_params.get("offset") or params.get("offset"), default=0)
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")
    active_only = parse_bool(
        query_params.get("activeOnly") or params.get("activeOnly"),
        default=True,
    )
    include_designations = parse_bool(
        query_params.get("includeDesignations") or params.get("includeDesignations"),
        default=False,
    )
    display_language = query_params.get("displayLanguage") or params.get(
        "displayLanguage"
    )
    terminology_version = (
        query_params.get("version")
        or query_params.get("terminologyVersion")
        or params.get("version")
        or params.get("terminologyVersion")
    )
    language = str(display_language).strip() if display_language else None
    return (
        str(text_filter).strip() if text_filter else None,
        count,
        offset,
        active_only,
        include_designations,
        language,
        str(terminology_version).strip() if terminology_version else None,
    )


def filter_property_name(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").lower()


def semantic_tag_values(value: Any) -> list[str]:
    values = value if isinstance(value, list | tuple) else str(value or "").split(",")
    return [str(item).strip() for item in values if str(item).strip()]


def include_to_ids(
    include: dict[str, Any],
    *,
    default_version: str | None = None,
) -> tuple[str, str | None, list[int], list[int], list[str] | None, str]:
    system = str(include.get("system") or "").strip()
    terminology_key = terminology_for_system(system)
    if not terminology_key:
        raise ValueError(f"Unsupported or missing code system: {system!r}")
    terminology_version = (
        str(include.get("version") or default_version or "").strip() or None
    )

    exact_ids: list[int] = []
    isa_ids: list[int] = []
    semantic_tags: list[str] = []
    for concept in include.get("concept") or []:
        if not isinstance(concept, dict) or not concept.get("code"):
            continue
        row = concept_by_fhir_code(
            str(concept["code"]),
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        if row is not None:
            exact_ids.append(int(row["concept_id"]))

    for item in include.get("filter") or []:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op") or "").strip()
        property_name = filter_property_name(item.get("property"))
        value = item.get("value")
        if not value:
            raise ValueError("ValueSet include filter.value is required")
        if property_name in {"concept", "code"}:
            if op != "is-a":
                raise ValueError("Only ValueSet concept filter op 'is-a' is supported")
            row = concept_by_fhir_code(
                str(value),
                terminology_key=terminology_key,
                terminology_version=terminology_version,
            )
            if row is None:
                raise ValueError(f"Unknown {system} code for is-a filter: {value}")
            isa_ids.append(int(row["concept_id"]))
            continue
        if property_name in {"semantictag", "semantic_tag"}:
            if op not in {"=", "in"}:
                raise ValueError(
                    "ValueSet semanticTag filter supports only '=' and 'in'"
                )
            semantic_tags.extend(semantic_tag_values(value))
            continue
        raise ValueError(
            "Only ValueSet include filter properties 'concept' and 'semanticTag' are supported"
        )

    return (
        terminology_key,
        terminology_version,
        exact_ids,
        isa_ids,
        semantic_tags or None,
        system,
    )


def concept_by_fhir_code(
    code: str,
    *,
    terminology_key: str,
    terminology_version: str | None = None,
) -> dict[str, Any] | None:
    row = get_concept_by_code(
        code,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    if row is not None:
        return row
    try:
        concept_id = int(str(code).strip())
    except ValueError:
        return None
    return get_concept(
        concept_id,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )


def coding_from_row(row: dict[str, Any], *, system: str) -> dict[str, Any]:
    code = code_from_row(row)
    coding = {
        "system": system,
        "code": str(code),
        "display": row.get("preferred_term") or row.get("fsn") or str(code),
    }
    if row.get("active") is False:
        coding["inactive"] = True
    return coding


def designation_language(value: Any, *, default: str | None = None) -> str | None:
    if not value:
        return default
    language = str(value).strip().lower()
    if not language:
        return default
    return language.split("-")[0]


def row_designations(
    row: dict[str, Any],
    *,
    display: str,
    default_language: str | None = None,
) -> list[dict[str, Any]]:
    seen: set[tuple[str | None, str, str]] = set()
    designations: list[dict[str, Any]] = []

    def add(value: Any, *, kind: str, language: Any = None) -> None:
        text = " ".join(str(value or "").split())
        if not text or text == display:
            return
        normalized_language = designation_language(language, default=default_language)
        key = (normalized_language, kind, text.casefold())
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {
            "value": text,
            "use": {
                "system": "http://terminology.hl7.org/CodeSystem/designation-usage",
                "code": kind,
            },
        }
        if normalized_language:
            item["language"] = normalized_language
        designations.append(item)

    for synonym in row.get("synonyms") or []:
        add(synonym, kind="synonym")
    for definition in row.get("text_definitions") or []:
        add(definition, kind="definition")
    for description in row.get("descriptions") or []:
        if not isinstance(description, dict):
            continue
        term = (
            description.get("term")
            or description.get("value")
            or description.get("display")
        )
        kind = str(description.get("type") or description.get("typeId") or "synonym")
        language = description.get("languageCode") or description.get("language")
        add(term, kind=kind, language=language)
    return designations


def result_from_row(
    row: dict[str, Any],
    *,
    system: str,
    include_designations: bool,
    display_language: str | None,
) -> dict[str, Any]:
    coding = coding_from_row(row, system=system)
    if not include_designations:
        return coding

    display = str(coding["display"])
    designations = row_designations(
        row,
        display=display,
        default_language=designation_language(display_language, default="en"),
    )
    result = {
        **coding,
        "active": bool(row.get("active", True)),
        "conceptId": str(row["concept_id"]),
        "semanticTag": row.get("semantic_tag"),
        "fsn": row.get("fsn"),
        "designations": designations,
    }
    return {key: value for key, value in result.items() if value not in (None, [], {})}


def append_coding(
    contains_by_key: dict[tuple[str, str], dict[str, Any]],
    *,
    row: dict[str, Any],
    system: str,
    include_designations: bool,
    display_language: str | None,
) -> None:
    coding = result_from_row(
        row,
        system=system,
        include_designations=include_designations,
        display_language=display_language,
    )
    contains_by_key.setdefault((coding["system"], coding["code"]), coding)


def code_from_row(row: dict[str, Any]) -> str:
    payload = row.get("payload")
    if isinstance(payload, dict):
        value = payload.get("displayCode") or payload.get("code")
        if value:
            return str(value)
    value = row.get("display_code") or row.get("code")
    if value:
        return str(value)
    return str(row["concept_id"])


def lookup_parameters(row: dict[str, Any], *, system: str) -> dict[str, Any]:
    code = code_from_row(row)
    display = row.get("preferred_term") or row.get("fsn") or str(code)
    parameters = [
        {"name": "name", "valueString": display},
        {"name": "display", "valueString": display},
        {"name": "code", "valueCode": str(code)},
        {"name": "system", "valueUri": system},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "inactive"},
                {"name": "value", "valueBoolean": not bool(row.get("active", True))},
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "internalConceptId"},
                {"name": "value", "valueString": str(row["concept_id"])},
            ],
        },
    ]
    semantic_tag = row.get("semantic_tag")
    if semantic_tag:
        parameters.append(
            {
                "name": "property",
                "part": [
                    {"name": "code", "valueCode": "semanticTag"},
                    {"name": "value", "valueString": str(semantic_tag)},
                ],
            }
        )
    for designation in row_designations(row, display=display, default_language="en"):
        parts = [
            {"name": "value", "valueString": designation["value"]},
        ]
        if designation.get("language"):
            parts.append({"name": "language", "valueCode": designation["language"]})
        use = designation.get("use") or {}
        if use.get("code"):
            parts.append({"name": "use", "valueCode": str(use["code"])})
        parameters.append({"name": "designation", "part": parts})
    return {"resourceType": "Parameters", "parameter": parameters}


def lexical_expand_include(
    *,
    terminology_key: str,
    terminology_version: str | None,
    exact_ids: list[int],
    isa_ids: list[int],
    semantic_tags: list[str] | None,
    text_filter: str | None,
    active_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    return expand_value_set_concepts(
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        concept_ids=exact_ids,
        isa_concept_ids=isa_ids,
        semantic_tags=semantic_tags,
        query=text_filter,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )


def vector_expand_include(
    *,
    terminology_key: str,
    terminology_version: str | None,
    exact_ids: list[int],
    isa_ids: list[int],
    semantic_tags: list[str] | None,
    text_filter: str,
    active_only: bool,
    limit: int,
    include_details: bool,
) -> list[dict[str, Any]]:
    rows_by_key: dict[int, dict[str, Any]] = {}
    lexical_rows, _ = expand_value_set_concepts(
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        concept_ids=exact_ids,
        isa_concept_ids=isa_ids,
        semantic_tags=semantic_tags,
        query=text_filter,
        active_only=active_only,
        limit=limit,
        offset=0,
    )
    for row in lexical_rows:
        rows_by_key.setdefault(int(row["concept_id"]), row)

    if exact_ids:
        exact_rows, _ = expand_value_set_concepts(
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            concept_ids=exact_ids,
            isa_concept_ids=[],
            semantic_tags=semantic_tags,
            query=text_filter,
            active_only=active_only,
            limit=limit,
            offset=0,
        )
        for row in exact_rows:
            rows_by_key.setdefault(int(row["concept_id"]), row)

    ancestor_ids: list[int | None] = isa_ids or (
        [None] if semantic_tags and not exact_ids else []
    )
    for ancestor_id in ancestor_ids:
        response = semantic_search_sync(
            query=text_filter,
            raw_embedding=None,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            model_key=embedding_model_key(),
            provider_override=None,
            provider_model_override=None,
            dimensions_override=None,
            limit=limit,
            ancestor_concept_id=ancestor_id,
            include_ancestor=True,
            active_only=active_only,
            include_details=include_details,
            include_query=False,
            semantic_tags=semantic_tags,
            vector_search_strategy=None,
            response_mode="vector",
        )
        for row in response["results"]:
            rows_by_key.setdefault(int(row["concept_id"]), row)
    return list(rows_by_key.values())


async def value_set_expand_endpoint(request: Request) -> JSONResponse:
    payload = await request_json_or_empty(request)
    value_set, params = value_set_from_payload(payload)
    if not value_set:
        return json_error(
            "A ValueSet resource or Parameters.valueSet is required", status_code=422
        )
    try:
        includes = value_set_includes(value_set)
        (
            text_filter,
            count,
            offset,
            active_only,
            include_designations,
            display_language,
            default_terminology_version,
        ) = parse_expand_options(
            request=request,
            params=params,
        )
        if not includes:
            raise ValueError("Only ValueSet.compose.include rules are supported")
    except ValueError as exc:
        return json_error(str(exc), status_code=422)

    contains_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    total = 0
    search_mode = "lexical"
    fallback_reason: str | None = None
    try:
        for include in includes:
            (
                terminology_key,
                terminology_version,
                exact_ids,
                isa_ids,
                semantic_tags,
                requested_system,
            ) = await run_in_threadpool(
                include_to_ids,
                include,
                default_version=default_terminology_version,
            )
            system = requested_system or system_for_terminology(terminology_key)
            if text_filter:
                try:
                    rows = await run_in_threadpool(
                        vector_expand_include,
                        terminology_key=terminology_key,
                        terminology_version=terminology_version,
                        exact_ids=exact_ids,
                        isa_ids=isa_ids,
                        semantic_tags=semantic_tags,
                        text_filter=text_filter,
                        active_only=active_only,
                        limit=count + offset,
                        include_details=include_designations,
                    )
                    search_mode = "vector"
                    total += len(rows)
                except Exception as exc:
                    fallback_reason = str(exc)
                    search_mode = "lexical"
                    rows, include_total = await run_in_threadpool(
                        lexical_expand_include,
                        terminology_key=terminology_key,
                        terminology_version=terminology_version,
                        exact_ids=exact_ids,
                        isa_ids=isa_ids,
                        semantic_tags=semantic_tags,
                        text_filter=text_filter,
                        active_only=active_only,
                        limit=count + offset,
                        offset=0,
                    )
                    total += include_total
            else:
                rows, include_total = await run_in_threadpool(
                    lexical_expand_include,
                    terminology_key=terminology_key,
                    terminology_version=terminology_version,
                    exact_ids=exact_ids,
                    isa_ids=isa_ids,
                    semantic_tags=semantic_tags,
                    text_filter=None,
                    active_only=active_only,
                    limit=count + offset,
                    offset=0,
                )
                total += include_total
            for row in rows:
                append_coding(
                    contains_by_key,
                    row=row,
                    system=system,
                    include_designations=include_designations,
                    display_language=display_language,
                )
    except ValueError as exc:
        return json_error(str(exc), status_code=422)
    except Exception as exc:
        return json_error(f"ValueSet expansion failed: {exc}", status_code=503)

    contains = list(contains_by_key.values())
    if search_mode != "vector" and not text_filter:
        contains = sorted(
            contains,
            key=lambda item: (item.get("display") or "", item.get("code") or ""),
        )
    page = contains[offset : offset + count]
    response = {
        "results": page,
        "resourceType": "ValueSet",
        "url": value_set.get("url"),
        "status": value_set.get("status", "active"),
        "expansion": {
            "timestamp": datetime.now(UTC).isoformat(),
            "total": max(total, len(contains)),
            "offset": offset,
            "contains": page,
        },
    }
    if text_filter:
        response["expansion"]["parameter"] = [
            {"name": "filter", "valueString": text_filter},
            {"name": "searchMode", "valueString": search_mode},
        ]
        if default_terminology_version:
            response["expansion"]["parameter"].append(
                {"name": "version", "valueString": default_terminology_version}
            )
        if fallback_reason:
            response["expansion"]["parameter"].append(
                {"name": "fallbackReason", "valueString": fallback_reason}
            )
    return json_response(response)


def lookup_args_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("resourceType") == "Parameters":
        return parameters_to_dict(payload)
    return payload


async def code_system_lookup_endpoint(request: Request) -> JSONResponse:
    payload = await request_json_or_empty(request)
    values = lookup_args_from_payload(payload)
    system = (
        request.query_params.get("system") or values.get("system") or values.get("url")
    )
    code = request.query_params.get("code") or values.get("code")
    terminology_version = (
        request.query_params.get("version")
        or request.query_params.get("terminologyVersion")
        or values.get("version")
        or values.get("terminologyVersion")
    )
    if not system or not code:
        return json_error("Both 'system' and 'code' are required", status_code=422)
    terminology_key = terminology_for_system(str(system))
    if not terminology_key:
        return json_error(f"Unsupported code system: {system!r}", status_code=422)
    row = await run_in_threadpool(
        concept_by_fhir_code,
        str(code),
        terminology_key=terminology_key,
        terminology_version=str(terminology_version) if terminology_version else None,
    )
    if row is None:
        return json_error("Code not found", status_code=404)
    return json_response(lookup_parameters(row, system=str(system)))


async def terminology_lookup_endpoint(request: Request) -> JSONResponse:
    terminology_key = str(request.path_params["terminology"]).strip()
    code = str(request.path_params["code"]).strip()
    terminology_version = request.query_params.get(
        "version"
    ) or request.query_params.get("terminologyVersion")
    if not terminology_key or not code:
        return json_error("terminology and code are required", status_code=422)
    row = await run_in_threadpool(
        concept_by_fhir_code,
        code,
        terminology_key=terminology_key,
        terminology_version=str(terminology_version) if terminology_version else None,
    )
    if row is None:
        return json_error("Code not found", status_code=404)
    return json_response(
        lookup_parameters(row, system=system_for_terminology(terminology_key))
    )
