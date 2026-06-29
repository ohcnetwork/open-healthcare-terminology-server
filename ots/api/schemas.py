from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


def _clean_optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = " ".join(str(value).split())
    return text or None


def _clean_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, list | tuple):
        candidates = value
    else:
        raise ValueError("must be a string array")
    return [text for item in candidates if (text := _clean_optional_string(item))]


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class TerminologyCreateRequest(ApiModel):
    key: str = Field(validation_alias=AliasChoices("key", "terminology", "system"))
    name: str | None = None
    description: str | None = None
    metadata: Any = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    connections: Any = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def clean_key(cls, value: str) -> str:
        cleaned = _clean_optional_string(value)
        if not cleaned:
            raise ValueError("key is required")
        return cleaned

    @field_validator("name", "description", mode="before")
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_string(value)

    @field_validator("keywords", mode="before")
    @classmethod
    def clean_keywords(cls, value: Any) -> list[str]:
        return _clean_string_list(value)


class CustomRecordUpsertRequest(ApiModel):
    code: str | None = None
    display: str | None = Field(
        default=None,
        validation_alias=AliasChoices("display", "name", "preferredTerm", "term"),
    )
    description: str | None = None
    metadata: Any = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    connections: Any = Field(default_factory=list)
    active: bool = True
    semantic_tag: str | None = Field(
        default=None, validation_alias=AliasChoices("semanticTag", "semantic_tag")
    )

    @field_validator("code", "display", "description", "semantic_tag", mode="before")
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_string(value)

    @field_validator("keywords", mode="before")
    @classmethod
    def clean_keywords(cls, value: Any) -> list[str]:
        return _clean_string_list(value)

    def storage_args(
        self, *, terminology_key: str, code_override: str | None = None
    ) -> dict[str, Any]:
        code = _clean_optional_string(code_override) or self.code
        if not code:
            raise ValueError("Field 'code' is required")
        return {
            "terminology_key": terminology_key,
            "code": code,
            "display": self.display,
            "description": self.description,
            "metadata": self.metadata,
            "keywords": self.keywords,
            "connections": self.connections,
            "active": self.active,
            "semantic_tag": self.semantic_tag,
        }


class EmbeddingPopulateRequest(ApiModel):
    terminology: str | None = None
    version: str | None = Field(
        default=None, validation_alias=AliasChoices("version", "terminologyVersion")
    )
    provider: str | None = None
    model: str | None = None
    model_key: str | None = Field(
        default=None, validation_alias=AliasChoices("modelKey", "model_key")
    )
    dimensions: int | None = None
    storage_type: str = Field(
        default="auto", validation_alias=AliasChoices("storageType", "storage_type")
    )
    provider_options: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("providerOptions", "provider_options"),
    )
    batch_size: int = Field(
        default=64, validation_alias=AliasChoices("batchSize", "batch_size")
    )
    parallel_requests: int | None = Field(
        default=None,
        validation_alias=AliasChoices("parallelRequests", "parallel_requests"),
    )
    limit: int | None = None
    after_concept_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("afterConceptId", "after_concept_id"),
    )
    refresh: bool = False
    include_inactive: bool = Field(
        default=False,
        validation_alias=AliasChoices("includeInactive", "include_inactive"),
    )
    semantic_tags: str | list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("semanticTags", "semantic_tags"),
    )
    all_semantic_tags: bool = Field(
        default=False,
        validation_alias=AliasChoices("allSemanticTags", "all_semantic_tags"),
    )
    ollama_host: str | None = Field(
        default=None, validation_alias=AliasChoices("ollamaHost", "ollama_host")
    )
    openai_timeout: float | None = Field(
        default=None,
        validation_alias=AliasChoices("openaiTimeout", "openai_timeout"),
    )
    openai_max_retries: int | None = Field(
        default=None,
        validation_alias=AliasChoices("openaiMaxRetries", "openai_max_retries"),
    )
    fastembed_cache_dir: str | None = Field(
        default=None,
        validation_alias=AliasChoices("fastembedCacheDir", "fastembed_cache_dir"),
    )
    fastembed_threads: int | None = Field(
        default=None,
        validation_alias=AliasChoices("fastembedThreads", "fastembed_threads"),
    )
    fastembed_providers: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("fastembedProviders", "fastembed_providers"),
    )
    max_input_chars: int | None = Field(
        default=None,
        validation_alias=AliasChoices("maxInputChars", "max_input_chars"),
    )
    skip_index: bool = Field(
        default=False, validation_alias=AliasChoices("skipIndex", "skip_index")
    )
    recreate_index: bool = Field(
        default=False,
        validation_alias=AliasChoices("recreateIndex", "recreate_index"),
    )

    @field_validator(
        "terminology",
        "version",
        "provider",
        "model",
        "model_key",
        "storage_type",
        "ollama_host",
        "fastembed_cache_dir",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        return _clean_optional_string(value)

    @field_validator("batch_size")
    @classmethod
    def validate_batch_size(cls, value: int) -> int:
        if value < 1:
            raise ValueError("batchSize must be greater than 0")
        return value

    @field_validator("parallel_requests")
    @classmethod
    def validate_parallel_requests(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("parallelRequests must be greater than 0")
        return value

    @field_validator("provider_options")
    @classmethod
    def validate_provider_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("providerOptions must be a JSON object")
        return value

    def task_payload(self) -> dict[str, Any]:
        payload = {
            "terminology": self.terminology,
            "version": self.version,
            "provider": self.provider,
            "model": self.model,
            "modelKey": self.model_key,
            "dimensions": self.dimensions,
            "storageType": self.storage_type,
            "providerOptions": self.provider_options,
            "batchSize": self.batch_size,
            "parallelRequests": self.parallel_requests,
            "limit": self.limit,
            "afterConceptId": self.after_concept_id,
            "refresh": self.refresh,
            "includeInactive": self.include_inactive,
            "semanticTags": self.semantic_tags,
            "allSemanticTags": self.all_semantic_tags,
            "ollamaHost": self.ollama_host,
            "openaiTimeout": self.openai_timeout,
            "openaiMaxRetries": self.openai_max_retries,
            "fastembedCacheDir": self.fastembed_cache_dir,
            "fastembedThreads": self.fastembed_threads,
            "fastembedProviders": self.fastembed_providers,
            "maxInputChars": self.max_input_chars,
            "skipIndex": self.skip_index,
            "recreateIndex": self.recreate_index,
        }
        return {key: value for key, value in payload.items() if value is not None}
