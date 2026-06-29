#!/usr/bin/env python3
"""Load SNOMED CT RF2 Snapshot files into the denormalized Postgres concept table."""

from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from psycopg.types.json import Jsonb

from ots import config
from ots.db.terminology_postgres import (
    concept_table_name,
    connect_db,
    init_schema,
)
from ots.terminology.snomed.model import SnomedTerminology
from ots.terminology.snomed.scripts.rf2_packages import (
    discover_snomed_rf2_packages,
    ensure_extracted_package,
    package_from_path,
)

csv.field_size_limit(sys.maxsize)
sys.setrecursionlimit(20_000)

TERMINOLOGY = SnomedTerminology()

SNOMED_IS_A_TYPE_ID = 116680003
SNOMED_FSN_TYPE_ID = 900000000000003001
SNOMED_SYNONYM_TYPE_ID = 900000000000013009
SNOMED_TEXT_DEFINITION_TYPE_ID = 900000000000550004
SNOMED_PREFERRED_ACCEPTABILITY_ID = 900000000000548007
SNOMED_ACCEPTABLE_ACCEPTABILITY_ID = 900000000000549004
SNOMED_US_ENGLISH_REFSET_ID = 900000000000509007
SNOMED_GB_ENGLISH_REFSET_ID = 900000000000508004
SNOMED_PRIMITIVE_ID = 900000000000074008
SNOMED_FULLY_DEFINED_ID = 900000000000073002

FILE_SPECS: dict[str, tuple[str, str]] = {
    "concept": ("Terminology", "sct2_Concept_Snapshot*.txt"),
    "description": ("Terminology", "sct2_Description_Snapshot*.txt"),
    "text_definition": ("Terminology", "sct2_TextDefinition_Snapshot*.txt"),
    "relationship": ("Terminology", "sct2_Relationship_Snapshot*.txt"),
    "relationship_concrete_value": (
        "Terminology",
        "sct2_RelationshipConcreteValues_Snapshot*.txt",
    ),
    "language_refset": ("Refset/Language", "der2_cRefset_LanguageSnapshot*.txt"),
    "simple_map": ("Refset/Map", "der2_sRefset_SimpleMapSnapshot*.txt"),
    "extended_map": ("Refset/Map", "der2_iisssccRefset_ExtendedMapSnapshot*.txt"),
    "simple_refset": ("Refset/Content", "der2_Refset_*Snapshot*.txt"),
    "attribute_value": ("Refset/Content", "der2_cRefset_AttributeValueSnapshot*.txt"),
    "association": ("Refset/Content", "der2_cRefset_Association*Snapshot*.txt"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rf2-dir",
        type=Path,
        default=Path("data/raw/snomed"),
        help=(
            "RF2 zip package, directory containing one RF2 zip package, "
            "Snapshot directory, or release root containing Snapshot/"
        ),
    )
    parser.add_argument(
        "--extract-dir",
        type=Path,
        default=Path("data/imports/snomed_rf2"),
        help="Workspace used when --force-extract extracts RF2 zip packages",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Extract RF2 zip packages before loading instead of streaming from zip",
    )
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL. OTS_DATABASE_URL is used by the API.",
    )
    parser.add_argument("--version", help="Terminology version key to import")
    parser.add_argument(
        "--base-version", help="Base/core edition version this edition composes"
    )
    parser.add_argument(
        "--package-key", help="Release package key registered for this import"
    )
    parser.add_argument(
        "--package-version", help="Release package version registered for this import"
    )
    parser.add_argument(
        "--package-type", default="release", help="Release package type"
    )
    parser.add_argument(
        "--package-role", default="primary", help="Package role in this edition"
    )
    parser.add_argument(
        "--package-source-uri", help="Original path/URI for the release package"
    )
    parser.add_argument(
        "--package-metadata-json",
        help="JSON object stored with the release package registration",
    )
    parser.add_argument(
        "--edition-type",
        choices=("standalone", "composed"),
        help="Edition type registered for this terminology version",
    )
    parser.add_argument(
        "--default-version",
        action="store_true",
        help="Mark this version as the default for the terminology",
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=config.EMBEDDING_DIMENSIONS,
        help="pgvector dimension for the empty embedding column",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2_000,
        help="Concept rows to upsert per Postgres batch",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit concepts for a smoke test",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Load inactive concepts too",
    )
    parser.add_argument(
        "--skip-optional",
        action="store_true",
        help="Skip maps, refsets, associations, concrete values, and attribute values",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the terminology-specific concept table before loading",
    )
    return parser.parse_args()


@dataclass(frozen=True)
class ZipRf2Member:
    zip_path: Path
    member: str

    def __str__(self) -> str:
        return f"{self.zip_path}!{self.member}"


@dataclass(frozen=True)
class DirectoryRf2Source:
    snapshot_dir: Path

    @property
    def label(self) -> str:
        return str(self.snapshot_dir)

    def files(self, name: str, *, required: bool = True) -> list[Path]:
        subdir, pattern = FILE_SPECS[name]
        matches = sorted((self.snapshot_dir / subdir).glob(pattern))
        if matches:
            return matches
        if required:
            raise FileNotFoundError(
                f"Could not find {pattern} under {self.snapshot_dir / subdir}"
            )
        return []


@dataclass(frozen=True)
class ZipRf2Source:
    zip_path: Path

    @property
    def label(self) -> str:
        return str(self.zip_path)

    def files(self, name: str, *, required: bool = True) -> list[ZipRf2Member]:
        subdir, pattern = FILE_SPECS[name]
        member_pattern = f"*/Snapshot/{subdir}/{pattern}"
        root_member_pattern = f"Snapshot/{subdir}/{pattern}"
        with zipfile.ZipFile(self.zip_path) as archive:
            matches = sorted(
                member
                for member in archive.namelist()
                if fnmatch.fnmatch(member, member_pattern)
                or fnmatch.fnmatch(member, root_member_pattern)
            )
        if matches:
            return [
                ZipRf2Member(zip_path=self.zip_path, member=member)
                for member in matches
            ]
        if required:
            raise FileNotFoundError(
                f"Could not find {pattern} under Snapshot/{subdir} in {self.zip_path}"
            )
        return []


Rf2File = Path | ZipRf2Member
Rf2Source = DirectoryRf2Source | ZipRf2Source


def open_rf2_source(
    path: Path,
    *,
    extract_dir: Path | None = None,
    force_extract: bool = False,
) -> Rf2Source:
    if path.is_file() and path.suffix.lower() == ".zip":
        if not force_extract:
            return ZipRf2Source(path)
        package = package_from_path(path)
        root = ensure_extracted_package(
            package,
            extract_dir=extract_dir or Path("data/imports/snomed_rf2"),
            force=force_extract,
        )
        return DirectoryRf2Source(root / "Snapshot")
    if (path / "Snapshot").is_dir():
        return DirectoryRf2Source(path / "Snapshot")
    if path.is_dir():
        packages = discover_snomed_rf2_packages(path)
        if packages:
            core_packages = [
                package for package in packages if package.package_type == "release"
            ]
            if len(packages) == 1:
                package = packages[0]
            elif len(core_packages) == 1:
                package = core_packages[0]
            else:
                raise ValueError(
                    f"Multiple RF2 packages found under {path}; pass --rf2-dir "
                    "with the exact package path"
                )
            return open_rf2_source(
                package.source_path,
                extract_dir=extract_dir,
                force_extract=force_extract,
            )
    return DirectoryRf2Source(path)


def rf2_files(source: Rf2Source, name: str, *, required: bool = True) -> list[Rf2File]:
    return source.files(name, required=required)


def rf2_file(source: Rf2Source, name: str, *, required: bool = True) -> Rf2File | None:
    matches = rf2_files(source, name, required=required)
    if matches:
        return matches[0]
    return None


def iter_rf2(path: Rf2File) -> Iterable[dict[str, str]]:
    if isinstance(path, ZipRf2Member):
        with (
            zipfile.ZipFile(path.zip_path) as archive,
            archive.open(path.member) as raw_handle,
            io.TextIOWrapper(raw_handle, encoding="utf-8", newline="") as handle,
        ):
            yield from csv.DictReader(handle, delimiter="\t")
        return
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def iter_rf2_optional(
    paths: Rf2File | Iterable[Rf2File] | None,
) -> Iterable[dict[str, str]]:
    if paths is None:
        return
    if isinstance(paths, Path | ZipRf2Member):
        yield from iter_rf2(paths)
        return
    for path in paths:
        yield from iter_rf2(path)


def is_active(row: dict[str, str]) -> bool:
    return row.get("active") == "1"


def label_acceptability(acceptability_id: int) -> str:
    if acceptability_id == SNOMED_PREFERRED_ACCEPTABILITY_ID:
        return "preferred"
    if acceptability_id == SNOMED_ACCEPTABLE_ACCEPTABILITY_ID:
        return "acceptable"
    return str(acceptability_id)


def label_description_type(type_id: int) -> str:
    if type_id == SNOMED_FSN_TYPE_ID:
        return "Fully specified name"
    if type_id == SNOMED_SYNONYM_TYPE_ID:
        return "Synonym"
    if type_id == SNOMED_TEXT_DEFINITION_TYPE_ID:
        return "Text definition"
    return str(type_id)


def label_definition_status(status_id: int) -> str:
    if status_id == SNOMED_PRIMITIVE_ID:
        return "Primitive"
    if status_id == SNOMED_FULLY_DEFINED_ID:
        return "Fully defined"
    return str(status_id)


def semantic_tag(fsn: str | None) -> str | None:
    if not fsn or not fsn.endswith(")"):
        return None
    start = fsn.rfind("(")
    if start == -1:
        return None
    tag = fsn[start + 1 : -1].strip()
    return tag or None


def unique_texts(values: Iterable[str], *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value).split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def load_concepts(
    path: Rf2File, *, include_inactive: bool, limit: int | None
) -> dict[int, dict[str, Any]]:
    concepts: dict[int, dict[str, Any]] = {}
    for row in iter_rf2(path):
        if not include_inactive and not is_active(row):
            continue
        concept_id = int(row["id"])
        concepts[concept_id] = {
            "concept_id": concept_id,
            "effective_time": int(row["effectiveTime"]),
            "active": is_active(row),
            "module_id": int(row["moduleId"]),
            "definition_status_id": int(row["definitionStatusId"]),
        }
        if limit is not None and len(concepts) >= limit:
            break
    return concepts


def load_descriptions(
    path: Rf2File,
    concepts: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    descriptions_by_id: dict[int, dict[str, Any]] = {}
    descriptions_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2(path):
        concept_id = int(row["conceptId"])
        if concept_id not in concepts or not is_active(row):
            continue
        description_id = int(row["id"])
        description = {
            "id": description_id,
            "effectiveTime": int(row["effectiveTime"]),
            "active": True,
            "moduleId": int(row["moduleId"]),
            "conceptId": concept_id,
            "languageCode": row["languageCode"],
            "typeId": int(row["typeId"]),
            "type": label_description_type(int(row["typeId"])),
            "term": row["term"],
            "caseSignificanceId": int(row["caseSignificanceId"]),
            "acceptability": [],
        }
        descriptions_by_id[description_id] = description
        descriptions_by_concept[concept_id].append(description)
    return descriptions_by_id, descriptions_by_concept


def apply_language_refset(
    path: Rf2File, descriptions_by_id: dict[int, dict[str, Any]]
) -> None:
    for row in iter_rf2(path):
        if not is_active(row):
            continue
        description = descriptions_by_id.get(int(row["referencedComponentId"]))
        if description is None:
            continue
        refset_id = int(row["refsetId"])
        acceptability_id = int(row["acceptabilityId"])
        description["acceptability"].append(
            {
                "refsetId": refset_id,
                "acceptabilityId": acceptability_id,
                "acceptability": label_acceptability(acceptability_id),
            }
        )
        if refset_id == SNOMED_US_ENGLISH_REFSET_ID:
            description["usAcceptabilityId"] = acceptability_id
        if refset_id == SNOMED_GB_ENGLISH_REFSET_ID:
            description["gbAcceptabilityId"] = acceptability_id


def load_text_definitions(
    path: Rf2File | None, concepts: dict[int, dict[str, Any]]
) -> dict[int, list[dict[str, Any]]]:
    definitions: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if path is None:
        return definitions
    for row in iter_rf2(path):
        concept_id = int(row["conceptId"])
        if concept_id not in concepts or not is_active(row):
            continue
        definitions[concept_id].append(
            {
                "id": int(row["id"]),
                "effectiveTime": int(row["effectiveTime"]),
                "active": True,
                "moduleId": int(row["moduleId"]),
                "conceptId": concept_id,
                "languageCode": row["languageCode"],
                "typeId": int(row["typeId"]),
                "type": "Text definition",
                "term": row["term"],
                "caseSignificanceId": int(row["caseSignificanceId"]),
            }
        )
    return definitions


def load_relationships(
    path: Rf2File,
    concepts: dict[int, dict[str, Any]],
) -> tuple[dict[int, list[int]], dict[int, list[int]], dict[int, list[dict[str, Any]]]]:
    parent_ids: dict[int, list[int]] = defaultdict(list)
    child_ids: dict[int, list[int]] = defaultdict(list)
    relationships: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2(path):
        source_id = int(row["sourceId"])
        if source_id not in concepts or not is_active(row):
            continue
        destination_id = int(row["destinationId"])
        type_id = int(row["typeId"])
        if type_id == SNOMED_IS_A_TYPE_ID:
            parent_ids[source_id].append(destination_id)
            child_ids[destination_id].append(source_id)
            continue
        relationships[source_id].append(
            {
                "id": int(row["id"]),
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "destinationId": destination_id,
                "relationshipGroup": int(row["relationshipGroup"]),
                "typeId": type_id,
                "characteristicTypeId": int(row["characteristicTypeId"]),
                "modifierId": int(row["modifierId"]),
            }
        )
    return parent_ids, child_ids, relationships


def load_concrete_values(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    values: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        source_id = int(row["sourceId"])
        if source_id not in concepts or not is_active(row):
            continue
        values[source_id].append(
            {
                "id": int(row["id"]),
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "value": row["value"],
                "relationshipGroup": int(row["relationshipGroup"]),
                "typeId": int(row["typeId"]),
                "characteristicTypeId": int(row["characteristicTypeId"]),
                "modifierId": int(row["modifierId"]),
            }
        )
    return values


def load_simple_maps(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    maps: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        concept_id = int(row["referencedComponentId"])
        if concept_id not in concepts or not is_active(row):
            continue
        maps[concept_id].append(
            {
                "id": row["id"],
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "refsetId": int(row["refsetId"]),
                "mapTarget": row["mapTarget"],
            }
        )
    return maps


def load_extended_maps(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    maps: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        concept_id = int(row["referencedComponentId"])
        if concept_id not in concepts or not is_active(row):
            continue
        maps[concept_id].append(
            {
                "id": row["id"],
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "refsetId": int(row["refsetId"]),
                "mapGroup": int(row["mapGroup"]),
                "mapPriority": int(row["mapPriority"]),
                "mapRule": row["mapRule"],
                "mapAdvice": row["mapAdvice"],
                "mapTarget": row["mapTarget"],
                "correlationId": int(row["correlationId"]),
                "mapCategoryId": int(row["mapCategoryId"]),
            }
        )
    return maps


def load_simple_refsets(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[int]]:
    refsets: dict[int, list[int]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        concept_id = int(row["referencedComponentId"])
        if concept_id in concepts and is_active(row):
            refsets[concept_id].append(int(row["refsetId"]))
    return refsets


def load_attribute_values(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    attributes: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        component_id = int(row["referencedComponentId"])
        if component_id not in concepts or not is_active(row):
            continue
        attributes[component_id].append(
            {
                "id": row["id"],
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "refsetId": int(row["refsetId"]),
                "valueId": int(row["valueId"]),
            }
        )
    return attributes


def load_associations(
    path: Rf2File | Iterable[Rf2File] | None,
    concepts: dict[int, dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    associations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_rf2_optional(path):
        concept_id = int(row["referencedComponentId"])
        if concept_id not in concepts or not is_active(row):
            continue
        associations[concept_id].append(
            {
                "id": row["id"],
                "effectiveTime": int(row["effectiveTime"]),
                "moduleId": int(row["moduleId"]),
                "refsetId": int(row["refsetId"]),
                "targetComponentId": int(row["targetComponentId"]),
            }
        )
    return associations


def concept_label(
    concept_id: int,
    descriptions_by_concept: dict[int, list[dict[str, Any]]],
) -> str:
    descriptions = descriptions_by_concept.get(concept_id, [])
    preferred = choose_preferred_term(descriptions)
    if preferred:
        return preferred
    fsn = choose_fsn(descriptions)
    return fsn or str(concept_id)


def choose_fsn(descriptions: list[dict[str, Any]]) -> str | None:
    for description in descriptions:
        if description["typeId"] == SNOMED_FSN_TYPE_ID:
            return str(description["term"])
    return None


def is_preferred(description: dict[str, Any], *, refset_id: int) -> bool:
    key = (
        "usAcceptabilityId"
        if refset_id == SNOMED_US_ENGLISH_REFSET_ID
        else "gbAcceptabilityId"
    )
    return description.get(key) == SNOMED_PREFERRED_ACCEPTABILITY_ID


def choose_preferred_term(descriptions: list[dict[str, Any]]) -> str | None:
    synonyms = [
        item for item in descriptions if item["typeId"] == SNOMED_SYNONYM_TYPE_ID
    ]
    for refset_id in (SNOMED_US_ENGLISH_REFSET_ID, SNOMED_GB_ENGLISH_REFSET_ID):
        for description in synonyms:
            if is_preferred(description, refset_id=refset_id):
                return str(description["term"])
    return str(synonyms[0]["term"]) if synonyms else None


def compute_ancestors(
    parent_ids: dict[int, list[int]], concepts: dict[int, dict[str, Any]]
) -> dict[int, list[int]]:
    cache: dict[int, tuple[int, ...]] = {}
    visiting: set[int] = set()

    def visit(concept_id: int) -> tuple[int, ...]:
        if concept_id in cache:
            return cache[concept_id]
        if concept_id in visiting:
            return ()
        visiting.add(concept_id)
        ordered: list[int] = []
        seen: set[int] = set()
        for parent_id in parent_ids.get(concept_id, []):
            if parent_id not in seen:
                seen.add(parent_id)
                ordered.append(parent_id)
            for ancestor_id in visit(parent_id):
                if ancestor_id not in seen:
                    seen.add(ancestor_id)
                    ordered.append(ancestor_id)
        visiting.remove(concept_id)
        cache[concept_id] = tuple(ordered)
        return cache[concept_id]

    return {concept_id: list(visit(concept_id)) for concept_id in concepts}


def enrich_relationships(
    relationships: list[dict[str, Any]],
    descriptions_by_concept: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for relationship in relationships:
        item = dict(relationship)
        item["typeTerm"] = concept_label(int(item["typeId"]), descriptions_by_concept)
        item["destinationTerm"] = concept_label(
            int(item["destinationId"]), descriptions_by_concept
        )
        enriched.append(item)
    return enriched


NOISY_SEARCH_TERMS = {
    "snomed ct concept",
    "clinical finding",
    "procedure",
    "situation with explicit context",
    "observable entity",
    "body structure",
    "organism",
    "substance",
    "pharmaceutical / biologic product",
    "specimen",
    "event",
    "environment",
    "environment / location",
    "qualifier value",
    "record artifact",
    "physical object",
    "social context",
    "staging and scales",
    "special concept",
    "metadata",
    "namespace concept",
}


def strip_semantic_tag(term: str | None) -> str:
    return re.sub(r"\s+\([^)]+\)\s*$", "", term or "").strip()


def is_noisy_search_term(term: str | None) -> bool:
    text = strip_semantic_tag(term)
    if not text:
        return True
    folded = text.casefold()
    if folded in NOISY_SEARCH_TERMS:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"[A-Z]\d{2}(?:\.\d+)?", text):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9]{4,}", text) and " " not in text:
        return True
    return bool(
        re.fullmatch(r"[A-Z0-9.]{3,}", text) and any(char.isdigit() for char in text)
    )


def clean_search_terms(values: Iterable[str | None]) -> list[str]:
    return unique_texts(
        strip_semantic_tag(value) for value in values if not is_noisy_search_term(value)
    )


def build_search_text(
    *,
    concept_id: int,
    fsn: str | None,
    preferred_term: str | None,
    tag: str | None,
    synonyms: list[str],
    definitions: list[str],
    parent_terms: list[str],
    relationships: list[dict[str, Any]],
    maps: dict[str, list[dict[str, Any]]],
) -> str:
    relationship_terms: list[str] = []
    for relationship in relationships[:50]:
        relationship_terms.append(str(relationship.get("destinationTerm", "")))
    source_terms = [
        fsn or "",
        preferred_term or "",
        *synonyms,
        *definitions,
        *parent_terms,
        *relationship_terms,
    ]
    terms = clean_search_terms(source_terms)
    if not terms:
        terms = unique_texts(
            strip_semantic_tag(value)
            for value in source_terms
            if strip_semantic_tag(value)
        )
    return "\n".join(terms)


UPSERT_SQL = """
    INSERT INTO {concept_table} (
        concept_id,
        active,
        effective_time,
        module_id,
        definition_status_id,
        definition_status,
        fsn,
        preferred_term,
        semantic_tag,
        synonyms,
        text_definitions,
        parent_ids,
        ancestor_ids,
        child_ids,
        descriptions,
        relationships,
        concrete_values,
        maps,
        associations,
        refset_ids,
        attributes,
        search_text,
        embedding,
        embedding_model,
        embedding_updated_at,
        payload,
        updated_at
    )
    VALUES (
        %(concept_id)s,
        %(active)s,
        %(effective_time)s,
        %(module_id)s,
        %(definition_status_id)s,
        %(definition_status)s,
        %(fsn)s,
        %(preferred_term)s,
        %(semantic_tag)s,
        %(synonyms)s,
        %(text_definitions)s,
        %(parent_ids)s,
        %(ancestor_ids)s,
        %(child_ids)s,
        %(descriptions)s,
        %(relationships)s,
        %(concrete_values)s,
        %(maps)s,
        %(associations)s,
        %(refset_ids)s,
        %(attributes)s,
        %(search_text)s,
        NULL,
        NULL,
        NULL,
        %(payload)s,
        now()
    )
    ON CONFLICT (concept_id) DO UPDATE SET
        active = excluded.active,
        effective_time = excluded.effective_time,
        module_id = excluded.module_id,
        definition_status_id = excluded.definition_status_id,
        definition_status = excluded.definition_status,
        fsn = excluded.fsn,
        preferred_term = excluded.preferred_term,
        semantic_tag = excluded.semantic_tag,
        synonyms = excluded.synonyms,
        text_definitions = excluded.text_definitions,
        parent_ids = excluded.parent_ids,
        ancestor_ids = excluded.ancestor_ids,
        child_ids = excluded.child_ids,
        descriptions = excluded.descriptions,
        relationships = excluded.relationships,
        concrete_values = excluded.concrete_values,
        maps = excluded.maps,
        associations = excluded.associations,
        refset_ids = excluded.refset_ids,
        attributes = excluded.attributes,
        search_text = excluded.search_text,
        embedding = NULL,
        embedding_model = NULL,
        embedding_updated_at = NULL,
        payload = excluded.payload,
        updated_at = now()
"""


def upsert_sql_for_table(table_name: str):
    return sql.SQL(UPSERT_SQL).format(concept_table=sql.Identifier(table_name))


def batched(
    items: Iterable[dict[str, Any]], batch_size: int
) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def build_documents(
    concepts: dict[int, dict[str, Any]],
    descriptions_by_concept: dict[int, list[dict[str, Any]]],
    text_definitions: dict[int, list[dict[str, Any]]],
    parent_ids: dict[int, list[int]],
    child_ids: dict[int, list[int]],
    ancestor_ids: dict[int, list[int]],
    relationships: dict[int, list[dict[str, Any]]],
    concrete_values: dict[int, list[dict[str, Any]]],
    simple_maps: dict[int, list[dict[str, Any]]],
    extended_maps: dict[int, list[dict[str, Any]]],
    simple_refsets: dict[int, list[int]],
    attributes: dict[int, list[dict[str, Any]]],
    associations: dict[int, list[dict[str, Any]]],
) -> Iterable[dict[str, Any]]:
    for concept_id, concept in concepts.items():
        descriptions = descriptions_by_concept.get(concept_id, [])
        definitions = text_definitions.get(concept_id, [])
        fsn = choose_fsn(descriptions)
        preferred_term = choose_preferred_term(descriptions)
        if preferred_term is None and fsn:
            preferred_term = strip_semantic_tag(fsn)
        if fsn is None and preferred_term:
            fsn = preferred_term
        tag = semantic_tag(fsn)
        synonyms = unique_texts(
            description["term"]
            for description in descriptions
            if description["typeId"] == SNOMED_SYNONYM_TYPE_ID
        )
        definition_terms = unique_texts(
            definition["term"] for definition in definitions
        )
        direct_parent_ids = list(dict.fromkeys(parent_ids.get(concept_id, [])))
        direct_child_ids = list(dict.fromkeys(child_ids.get(concept_id, [])))
        all_ancestor_ids = list(dict.fromkeys(ancestor_ids.get(concept_id, [])))
        parent_terms = [
            concept_label(parent_id, descriptions_by_concept)
            for parent_id in direct_parent_ids
        ]
        relationship_rows = enrich_relationships(
            relationships.get(concept_id, []),
            descriptions_by_concept,
        )
        maps = {
            "simple": simple_maps.get(concept_id, []),
            "extended": extended_maps.get(concept_id, []),
        }
        search_text = build_search_text(
            concept_id=concept_id,
            fsn=fsn,
            preferred_term=preferred_term,
            tag=tag,
            synonyms=synonyms,
            definitions=definition_terms,
            parent_terms=parent_terms,
            relationships=relationship_rows,
            maps=maps,
        )
        payload = {
            "concept": concept,
            "parentTerms": parent_terms,
            "ancestorCount": len(all_ancestor_ids),
            "childCount": len(direct_child_ids),
        }
        yield {
            "concept_id": concept_id,
            "active": concept["active"],
            "effective_time": concept["effective_time"],
            "module_id": concept["module_id"],
            "definition_status_id": concept["definition_status_id"],
            "definition_status": label_definition_status(
                concept["definition_status_id"]
            ),
            "fsn": fsn,
            "preferred_term": preferred_term,
            "semantic_tag": tag,
            "synonyms": synonyms,
            "text_definitions": definition_terms,
            "parent_ids": direct_parent_ids,
            "ancestor_ids": all_ancestor_ids,
            "child_ids": direct_child_ids,
            "descriptions": Jsonb([*descriptions, *definitions]),
            "relationships": Jsonb(relationship_rows),
            "concrete_values": Jsonb(concrete_values.get(concept_id, [])),
            "maps": Jsonb(maps),
            "associations": Jsonb(associations.get(concept_id, [])),
            "refset_ids": list(dict.fromkeys(simple_refsets.get(concept_id, []))),
            "attributes": Jsonb(attributes.get(concept_id, [])),
            "search_text": search_text,
            "payload": Jsonb(payload),
        }


def parse_package_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    metadata = json.loads(value)
    if not isinstance(metadata, dict):
        raise ValueError("--package-metadata-json must be a JSON object")
    return metadata


def load_snomed_rf2_package(args: argparse.Namespace) -> dict[str, Any]:
    start = time.perf_counter()
    rf2_source = open_rf2_source(
        args.rf2_dir,
        extract_dir=getattr(args, "extract_dir", None),
        force_extract=bool(getattr(args, "force_extract", False)),
    )

    print(f"RF2 source: {rf2_source.label}", flush=True)
    print("Loading concepts", flush=True)
    concepts = load_concepts(
        rf2_file(rf2_source, "concept"),
        include_inactive=args.include_inactive,
        limit=args.limit,
    )
    print(f"Concepts: {len(concepts):,}", flush=True)

    print("Loading descriptions", flush=True)
    descriptions_by_id, descriptions_by_concept = load_descriptions(
        rf2_file(rf2_source, "description"),
        concepts,
    )
    print(f"Descriptions: {len(descriptions_by_id):,}", flush=True)

    print("Applying language refset", flush=True)
    apply_language_refset(rf2_file(rf2_source, "language_refset"), descriptions_by_id)

    print("Loading text definitions", flush=True)
    text_definitions = load_text_definitions(
        rf2_file(rf2_source, "text_definition", required=False),
        concepts,
    )

    print("Loading relationships", flush=True)
    parent_ids, child_ids, relationships = load_relationships(
        rf2_file(rf2_source, "relationship"),
        concepts,
    )

    if args.skip_optional:
        concrete_values: dict[int, list[dict[str, Any]]] = defaultdict(list)
        simple_maps: dict[int, list[dict[str, Any]]] = defaultdict(list)
        extended_maps: dict[int, list[dict[str, Any]]] = defaultdict(list)
        simple_refsets: dict[int, list[int]] = defaultdict(list)
        attributes: dict[int, list[dict[str, Any]]] = defaultdict(list)
        associations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    else:
        print("Loading optional RF2 payloads", flush=True)
        concrete_values = load_concrete_values(
            rf2_files(rf2_source, "relationship_concrete_value", required=False),
            concepts,
        )
        simple_maps = load_simple_maps(
            rf2_files(rf2_source, "simple_map", required=False), concepts
        )
        extended_maps = load_extended_maps(
            rf2_files(rf2_source, "extended_map", required=False), concepts
        )
        simple_refsets = load_simple_refsets(
            rf2_files(rf2_source, "simple_refset", required=False), concepts
        )
        attributes = load_attribute_values(
            rf2_files(rf2_source, "attribute_value", required=False), concepts
        )
        associations = load_associations(
            rf2_files(rf2_source, "association", required=False), concepts
        )

    print("Precomputing ancestor arrays", flush=True)
    ancestors = compute_ancestors(parent_ids, concepts)

    print(f"Connecting to Postgres: {args.database_url}", flush=True)
    config.set_database_url(args.database_url)
    terminology_key = TERMINOLOGY.key
    concept_table = concept_table_name(terminology_key, args.version)
    source_package_path = (
        rf2_source.zip_path
        if isinstance(rf2_source, ZipRf2Source)
        else args.rf2_dir
        if args.rf2_dir.is_file() and args.rf2_dir.suffix.lower() == ".zip"
        else None
    )
    source_package = (
        package_from_path(source_package_path) if source_package_path else None
    )
    package_metadata = parse_package_metadata(args.package_metadata_json)
    if source_package is not None:
        package_metadata = {**source_package.metadata, **package_metadata}
    with connect_db() as conn:
        if args.recreate:
            print(f"Dropping existing {concept_table}", flush=True)
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {concept_table} CASCADE").format(
                    concept_table=sql.Identifier(concept_table)
                )
            )
            conn.commit()
        init_schema(
            conn,
            embedding_dimensions=args.embedding_dimensions,
            terminology_key=terminology_key,
            terminology_version=args.version,
            set_default_version=args.default_version,
            edition_type=args.edition_type,
            base_version_key=args.base_version,
            package_key=args.package_key
            or (source_package.package_key if source_package else None),
            package_version=args.package_version
            or (source_package.package_version if source_package else None),
            package_type=(
                args.package_type
                if args.package_type != "release" or source_package is None
                else source_package.package_type
            ),
            package_role=args.package_role,
            package_source_uri=args.package_source_uri
            or (source_package.source_uri if source_package else None),
            package_metadata=package_metadata,
        )
        upsert_sql = upsert_sql_for_table(concept_table)

        total = 0
        document_iter = build_documents(
            concepts,
            descriptions_by_concept,
            text_definitions,
            parent_ids,
            child_ids,
            ancestors,
            relationships,
            concrete_values,
            simple_maps,
            extended_maps,
            simple_refsets,
            attributes,
            associations,
        )
        for batch in batched(document_iter, args.batch_size):
            with conn.cursor() as cur:
                cur.executemany(upsert_sql, batch)
            conn.commit()
            total += len(batch)
            elapsed = max(time.perf_counter() - start, 1e-9)
            print(
                f"Upserted {total:,}/{len(concepts):,} concepts ({total / elapsed:.1f}/s)",
                flush=True,
            )

    elapsed = time.perf_counter() - start
    print(f"Done in {elapsed:.1f}s")
    return {
        "rf2_source": rf2_source.label,
        "concept_table": concept_table,
        "concepts": len(concepts),
        "upserted": total,
        "elapsed_seconds": elapsed,
    }


def main() -> int:
    args = parse_args()
    load_snomed_rf2_package(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
