from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TERMINOLOGY_KEY = "snomed"
DEFAULT_TERMINOLOGY_VERSION_KEY = "current"
IMPORTED_TERMINOLOGY_KIND = "imported"
CUSTOM_TERMINOLOGY_KIND = "custom"


def normalize_terminology_key(terminology_key: str | None = None) -> str:
    value = (terminology_key or DEFAULT_TERMINOLOGY_KEY).strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    if not value:
        raise ValueError("terminology must not be empty")
    return value


def normalize_version_key(version_key: str | None = None) -> str:
    value = (version_key or DEFAULT_TERMINOLOGY_VERSION_KEY).strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    if not value:
        raise ValueError("terminology version must not be empty")
    return value


def concept_table_name(terminology_key: str | None = None) -> str:
    return f"{normalize_terminology_key(terminology_key)}_concept_document"


def versioned_concept_table_name(
    terminology_key: str | None = None,
    version_key: str | None = None,
) -> str:
    normalized_version = normalize_version_key(version_key)
    if normalized_version == DEFAULT_TERMINOLOGY_VERSION_KEY:
        return concept_table_name(terminology_key)
    return (
        f"{normalize_terminology_key(terminology_key)}__"
        f"{normalized_version}_concept_document"
    )


def build_search_text(*values: Any) -> str:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, dict):
            candidates = [
                str(item) for item in value.values() if item not in (None, "")
            ]
        elif isinstance(value, list | tuple | set):
            candidates = [str(item) for item in value if item not in (None, "")]
        else:
            candidates = [str(value)]
        for candidate in candidates:
            text = " ".join(str(candidate).split())
            if not text:
                continue
            key = text.lower()
            if key not in seen:
                seen.add(key)
                terms.append(text)
    return " | ".join(terms)


def stable_code_concept_id(terminology_key: str, code: str) -> int:
    normalized_key = normalize_terminology_key(terminology_key)
    normalized_code = str(code).strip()
    if not normalized_code:
        raise ValueError("code is required")
    digest = hashlib.sha256(f"{normalized_key}:{normalized_code}".encode()).hexdigest()
    return int(digest[:15], 16)


class TerminologyRowModel(BaseModel):
    """Common denormalized concept row shape used by terminology tables."""

    model_config = ConfigDict(extra="allow")

    concept_id: int
    active: bool
    effective_time: int | None = None
    module_id: int | None = None
    definition_status_id: int | None = None
    definition_status: str | None = None
    fsn: str | None = None
    preferred_term: str | None = None
    semantic_tag: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    text_definitions: list[str] = Field(default_factory=list)
    parent_ids: list[int] = Field(default_factory=list)
    ancestor_ids: list[int] = Field(default_factory=list)
    child_ids: list[int] = Field(default_factory=list)
    descriptions: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    concrete_values: list[dict[str, Any]] = Field(default_factory=list)
    maps: Any = None
    associations: list[dict[str, Any]] = Field(default_factory=list)
    refset_ids: list[int] = Field(default_factory=list)
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    search_text: str
    embedding: list[float] | None = None
    embedding_model: str | None = None
    embedding_updated_at: datetime | None = None
    payload: dict[str, Any]
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TerminologyDefinition:
    key: str
    name: str
    kind: str
    description: str | None = None
    code_system_uri: str | None = None
    row_model: ClassVar[type[TerminologyRowModel]] = TerminologyRowModel

    @property
    def concept_table(self) -> str:
        return concept_table_name(self.key)

    @property
    def is_imported(self) -> bool:
        return self.kind == IMPORTED_TERMINOLOGY_KIND

    @property
    def is_custom(self) -> bool:
        return self.kind == CUSTOM_TERMINOLOGY_KIND

    def code_to_concept_id(self, code: str) -> int:
        return stable_code_concept_id(self.key, code)

    def record_search_text(
        self,
        *,
        display: str | None,
        description: str | None,
        keywords: list[str],
    ) -> str:
        return build_search_text(display, description, keywords)


class CustomTerminology(TerminologyDefinition):
    def __init__(
        self,
        key: str,
        *,
        name: str | None = None,
        description: str | None = None,
        code_system_uri: str | None = None,
    ) -> None:
        normalized_key = normalize_terminology_key(key)
        super().__init__(
            key=normalized_key,
            name=name or normalized_key,
            kind=CUSTOM_TERMINOLOGY_KIND,
            description=description,
            code_system_uri=code_system_uri,
        )
