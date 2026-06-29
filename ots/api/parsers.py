from __future__ import annotations

from typing import Any

from ots import config


def parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def parse_limit(value: Any, *, default: int = 25, maximum: int = 100) -> int:
    limit = int(value or default)
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def parse_optional_int(value: Any, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive SCTID") from exc
    if parsed < 1:
        raise ValueError(f"{field} must be a positive SCTID")
    return parsed


def parse_include_details(source: Any, *, default: bool = False) -> bool:
    detail = source.get("detail") if hasattr(source, "get") else None
    if detail is not None:
        normalized = str(detail).strip().lower()
        if normalized in {"basic", "summary", "false", "0"}:
            return False
        if normalized in {"full", "all", "details", "true", "1"}:
            return True
        raise ValueError("detail must be one of: basic, full")
    value = source.get("includeDetails") if hasattr(source, "get") else None
    return parse_bool(value, default=default)


def parse_include_query(source: Any, *, default: bool = False) -> bool:
    if not hasattr(source, "get"):
        return default
    value = source.get("showQuery")
    if value is None:
        value = source.get("includeQuery")
    return parse_bool(value, default=default)


def parse_terminology(source: Any) -> str:
    if hasattr(source, "get"):
        value = source.get("terminology", source.get("system"))
        if value:
            return str(value)
    return config.TERMINOLOGY_KEY


def parse_terminology_version(source: Any) -> str | None:
    if hasattr(source, "get"):
        value = source.get("version")
        if value is None:
            value = source.get("terminologyVersion")
        if value:
            return str(value)
    return None


def parse_semantic_tags(source: Any) -> list[str] | None:
    if not hasattr(source, "get"):
        return None
    values: list[Any] = []
    if hasattr(source, "getlist"):
        values.extend(source.getlist("semanticTag"))
        values.extend(source.getlist("semanticTags"))
    else:
        for key in ("semanticTag", "semanticTags"):
            value = source.get(key)
            if value is not None:
                values.append(value)
    tags: list[str] = []
    for value in values:
        if isinstance(value, list | tuple):
            tags.extend(str(item).strip() for item in value)
        else:
            tags.extend(item.strip() for item in str(value).split(","))
    tags = [tag for tag in tags if tag]
    return tags or None


def parse_search_mode(source: Any) -> str:
    raw_mode = None
    if hasattr(source, "get"):
        raw_mode = source.get("mode", source.get("searchMode"))
    if raw_mode is not None:
        normalized = str(raw_mode).strip().lower()
        if normalized in {"lexical", "text", "postgres", "fts"}:
            return "lexical"
        if normalized in {"vector", "semantic", "embedding", "embeddings"}:
            return "vector"
        raise ValueError("mode must be one of: lexical, vector")
    value = source.get("useEmbeddings") if hasattr(source, "get") else None
    if parse_bool(value, default=False):
        return "vector"
    return "lexical"


def parse_vector_search_strategy(source: Any) -> str | None:
    if not hasattr(source, "get"):
        return None
    raw_strategy = source.get("vectorSearchStrategy", source.get("searchStrategy"))
    if raw_strategy is None:
        return None
    strategy = str(raw_strategy).strip().lower()
    valid = {"halfvec_rerank", "full_exact", "halfvec_only"}
    if strategy not in valid:
        raise ValueError(
            "vectorSearchStrategy must be one of: halfvec_rerank, full_exact, halfvec_only"
        )
    return strategy


def has_explicit_search_mode(source: Any) -> bool:
    return hasattr(source, "get") and (
        source.get("mode") is not None or source.get("searchMode") is not None
    )
