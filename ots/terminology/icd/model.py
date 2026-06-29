from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ots.terminology.base import (
    IMPORTED_TERMINOLOGY_KIND,
    TerminologyDefinition,
    TerminologyRowModel,
)


class Icd10CmPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    rawCode: str
    displayCode: str
    preferredTerm: str
    shortTitle: str
    billable: bool
    sortOrder: int
    terminology: str
    source: str


class Icd10CmConceptRow(TerminologyRowModel):
    payload: Icd10CmPayload


class Icd11Payload(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    icdCode: str | None = None
    blockId: str | None = None
    externalId: str
    title: str
    classKind: str
    depthInKind: str | None = None
    isResidual: bool
    isLeaf: bool
    primaryTabulation: bool
    chapterNo: str | None = None
    browserLink: str | None = None
    foundationUri: str | None = None
    linearizationUri: str | None = None
    parentUri: str | None = None
    terminology: str
    source: str


class Icd11ConceptRow(TerminologyRowModel):
    payload: Icd11Payload


class Icd10CmTerminology(TerminologyDefinition):
    row_model: ClassVar[type[Icd10CmConceptRow]] = Icd10CmConceptRow

    def __init__(self) -> None:
        super().__init__(
            key="icd10cm",
            name="ICD-10-CM",
            kind=IMPORTED_TERMINOLOGY_KIND,
            description="Imported ICD-10-CM diagnosis code release data",
            code_system_uri="http://hl7.org/fhir/sid/icd-10-cm",
        )


class Icd11Terminology(TerminologyDefinition):
    row_model: ClassVar[type[Icd11ConceptRow]] = Icd11ConceptRow

    def __init__(self) -> None:
        super().__init__(
            key="icd11",
            name="ICD-11 MMS",
            kind=IMPORTED_TERMINOLOGY_KIND,
            description="Imported ICD-11 Mortality and Morbidity Statistics release data",
            code_system_uri="https://icd.who.int/browse11/l-m/en",
        )


ICD10CM_TERMINOLOGY = Icd10CmTerminology()
ICD11_TERMINOLOGY = Icd11Terminology()
