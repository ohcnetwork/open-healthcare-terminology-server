from __future__ import annotations

from ots.terminology.base import (
    CustomTerminology,
    TerminologyDefinition,
    normalize_terminology_key,
)
from ots.terminology.icd import ICD10CM_TERMINOLOGY, ICD11_TERMINOLOGY
from ots.terminology.loinc import TERMINOLOGY as LOINC_TERMINOLOGY
from ots.terminology.snomed import TERMINOLOGY as SNOMED_TERMINOLOGY

REGISTERED_TERMINOLOGIES: dict[str, TerminologyDefinition] = {
    terminology.key: terminology
    for terminology in (
        SNOMED_TERMINOLOGY,
        LOINC_TERMINOLOGY,
        ICD10CM_TERMINOLOGY,
        ICD11_TERMINOLOGY,
    )
}
IMPORTED_TERMINOLOGIES = REGISTERED_TERMINOLOGIES
IMPORTED_TERMINOLOGY_KEYS = frozenset(REGISTERED_TERMINOLOGIES)


def get_terminology_definition(
    terminology_key: str | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    code_system_uri: str | None = None,
) -> TerminologyDefinition:
    normalized_key = normalize_terminology_key(terminology_key)
    imported = REGISTERED_TERMINOLOGIES.get(normalized_key)
    if imported is not None:
        return imported
    return CustomTerminology(
        normalized_key,
        name=name,
        description=description,
        code_system_uri=code_system_uri,
    )
