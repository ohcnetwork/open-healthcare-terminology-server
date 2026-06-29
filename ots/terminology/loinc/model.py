from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ots.terminology.base import (
    IMPORTED_TERMINOLOGY_KIND,
    TerminologyDefinition,
    TerminologyRowModel,
)


class LoincPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    terminology: str = "loinc"
    code: str
    loincNumber: str
    displayCode: str
    status: str | None = None
    versionLastChanged: str | None = None
    versionFirstReleased: str | None = None
    row: dict[str, Any]
    panelRows: list[dict[str, Any]] = Field(default_factory=list)
    consumerNames: list[str] = Field(default_factory=list)
    linguisticVariantCount: int = 0
    parentCodes: list[str] = Field(default_factory=list)
    childCodes: list[str] = Field(default_factory=list)
    ancestorCodes: list[str] = Field(default_factory=list)


class LoincConceptRow(TerminologyRowModel):
    payload: LoincPayload


class LoincTerminology(TerminologyDefinition):
    row_model: ClassVar[type[LoincConceptRow]] = LoincConceptRow

    def __init__(self) -> None:
        super().__init__(
            key="loinc",
            name="LOINC",
            kind=IMPORTED_TERMINOLOGY_KIND,
            description="Imported LOINC release data",
            code_system_uri="http://loinc.org",
        )

    def code_to_concept_id(self, code: str) -> int:
        normalized = str(code).strip().replace("-", "")
        if normalized.isdigit():
            return int(normalized)
        return super().code_to_concept_id(code)


TERMINOLOGY = LoincTerminology()
