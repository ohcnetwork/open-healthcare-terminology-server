from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ots.terminology.base import (
    IMPORTED_TERMINOLOGY_KIND,
    TerminologyDefinition,
    TerminologyRowModel,
)


class SnomedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    concept: dict[str, Any]
    parentTerms: list[str] = Field(default_factory=list)
    ancestorCount: int = 0
    childCount: int = 0


class SnomedConceptRow(TerminologyRowModel):
    payload: SnomedPayload


class SnomedTerminology(TerminologyDefinition):
    row_model: ClassVar[type[SnomedConceptRow]] = SnomedConceptRow

    def __init__(self) -> None:
        super().__init__(
            key="snomed",
            name="SNOMED CT",
            kind=IMPORTED_TERMINOLOGY_KIND,
            description="Imported SNOMED CT release data",
            code_system_uri="http://snomed.info/sct",
        )

    def code_to_concept_id(self, code: str) -> int:
        return int(str(code).strip())


TERMINOLOGY = SnomedTerminology()
