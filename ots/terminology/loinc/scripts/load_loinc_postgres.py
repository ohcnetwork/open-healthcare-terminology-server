#!/usr/bin/env python3
"""Load LOINC CSV release files into a denormalized Postgres concept table."""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from psycopg.types.json import Jsonb

from ots import config
from ots.db.terminology_postgres import (
    DEFAULT_DATABASE_URL,
    DEFAULT_EMBEDDING_DIMENSIONS,
    connect_db,
    concept_table_name,
    init_schema,
)
from ots.terminology.loinc.model import LoincTerminology

csv.field_size_limit(sys.maxsize)
sys.setrecursionlimit(20_000)

TERMINOLOGY = LoincTerminology()
DEFAULT_TERMINOLOGY = TERMINOLOGY.key
LOINC_MODULE_ID = 0
LOINC_DEFINITION_STATUS_ID = 0

TEXT_FIELDS = (
    "COMPONENT",
    "DefinitionDescription",
    "CONSUMER_NAME",
    "EXMPL_ANSWERS",
    "SURVEY_QUEST_TEXT",
    "RELATEDNAMES2",
    "SHORTNAME",
    "LONG_COMMON_NAME",
    "DisplayName",
)

NOISY_LOINC_TERMS = {
    "acceptable",
    "active",
    "both",
    "chemistry",
    "clinical",
    "component",
    "detailedmodel",
    "documents",
    "display_name",
    "en",
    "endocrine",
    "endocrinology",
    "hematology/cell counts",
    "laboratory",
    "level",
    "long_common_name",
    "metadata",
    "point in time",
    "primary",
    "property",
    "preferred",
    "quan",
    "quant",
    "quantitative",
    "random",
    "related_name",
    "scale",
    "search",
    "short_name",
    "syntaxenhancement",
    "system",
    "time",
    "universallaborders",
}

NOISY_LOINC_CODES = {
    "acnc",
    "arb",
    "bld",
    "doc",
    "hx",
    "mcnc",
    "mfr",
    "nar",
    "ncinc",
    "ncnc",
    "nom",
    "ord",
    "pl",
    "plsm",
    "pt",
    "qn",
    "qnt",
    "scnc",
    "ser/plas",
    "serp",
    "serpl",
    "serplas",
    "sr",
    "tot",
    "totl",
    "wb",
}


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--loinc-dir",
        type=Path,
        default=Path("Loinc_2.82"),
        help="LOINC release root directory",
    )
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL. OTS_DATABASE_URL is used by the API.",
    )
    parser.add_argument("--version", help="Terminology version key to import")
    parser.add_argument("--base-version", help="Base/core edition version this edition composes")
    parser.add_argument("--package-key", help="Release package key registered for this import")
    parser.add_argument("--package-version", help="Release package version registered for this import")
    parser.add_argument("--package-type", default="release", help="Release package type")
    parser.add_argument(
        "--default-version",
        action="store_true",
        help="Mark this version as the default for the terminology",
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=config.EMBEDDING_DIMENSIONS,
        help="pgvector dimension for the empty compatibility embedding column",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2_000,
        help="Concept rows to upsert per Postgres batch",
    )
    parser.add_argument("--limit", type=int, help="Limit LOINC terms for a smoke test")
    parser.add_argument(
        "--include-deprecated",
        action="store_true",
        help="Load deprecated LOINC terms too",
    )
    parser.add_argument(
        "--skip-linguistic-variants",
        action="store_true",
        help="Skip language variant files",
    )
    parser.add_argument(
        "--skip-optional",
        action="store_true",
        help="Skip parts, groups, panels, answers, maps, consumers, and variants",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the terminology-specific concept table before loading",
    )
    return parser.parse_args()


def read_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def optional_csv(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return []
    return read_csv(path)


def clean(value: Any) -> str:
    return str(value or "").strip()


def unique_texts(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = clean(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def split_related_names(value: str) -> list[str]:
    text = clean(value)
    if not text:
        return []
    pieces = re.split(r"[;|]", text)
    if len(pieces) == 1:
        return [text]
    return unique_texts(pieces)


def split_search_values(value: Any) -> list[str]:
    text = html.unescape(clean(value))
    text = re.sub(r"\s+\(LOINC\)\s*$", "", text)
    if not text:
        return []
    pieces = re.split(r"[;|]", text)
    return unique_texts(pieces)


def is_noisy_loinc_search_term(value: str | None) -> bool:
    text = html.unescape(clean(value))
    if not text:
        return True
    folded = text.casefold()
    if folded in NOISY_LOINC_TERMS or folded in NOISY_LOINC_CODES:
        return True
    if len(text) == 1:
        return True
    if text.startswith(("http://", "https://")):
        return True
    if folded in {"loinc", "logical observation identifiers names and codes"}:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.fullmatch(r"\d+-\d", text):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)+", text):
        return True
    if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", text):
        return True
    if re.fullmatch(r"L[APG]\d+-\d", text):
        return True
    if re.fullmatch(r"LG\d+-\d", text):
        return True
    compact_tokens = [
        token.casefold()
        for token in re.split(r"[^A-Za-z0-9/]+", text)
        if token
    ]
    if len(compact_tokens) <= 4 and any(token in NOISY_LOINC_CODES for token in compact_tokens):
        return True
    if " " not in text and any(char in text for char in ("-", "+", ".")) and len(text) <= 16:
        return True
    if re.fullmatch(r"[A-Z]{1,5}/[A-Za-z]{1,5}", text) and " " not in text:
        return True
    if re.fullmatch(r"[A-Z]{2,8}", text) and folded not in {"hba1c"}:
        return True
    return False


def clean_loinc_search_terms(values: Iterable[Any]) -> list[str]:
    terms: list[str] = []
    for value in values:
        for piece in split_search_values(value):
            if not is_noisy_loinc_search_term(piece):
                terms.append(piece)
    return unique_texts(terms)


def loinc_code_to_id(code: str) -> int:
    normalized = clean(code)
    if not re.fullmatch(r"\d+-\d", normalized):
        raise ValueError(f"Invalid LOINC code: {code!r}")
    return TERMINOLOGY.code_to_concept_id(normalized)


def version_to_int(value: str) -> int:
    text = clean(value)
    if not text:
        return 0
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits or 0)


def is_active_loinc(row: dict[str, str]) -> bool:
    return clean(row.get("STATUS")).upper() not in {"DEPRECATED", "DISCOURAGED"}


def is_deprecated_loinc(row: dict[str, str]) -> bool:
    return clean(row.get("STATUS")).upper() == "DEPRECATED"


def semantic_tag(row: dict[str, str]) -> str:
    return clean(row.get("CLASS")).lower() or "loinc"


def upsert_sql_for_table(table_name: str):
    return sql.SQL(UPSERT_SQL).format(concept_table=sql.Identifier(table_name))


def batched(items: Iterable[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_loinc_terms(
    loinc_dir: Path,
    *,
    include_deprecated: bool,
    limit: int | None,
) -> dict[str, dict[str, str]]:
    path = loinc_dir / "LoincTable" / "Loinc.csv"
    terms: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        code = clean(row.get("LOINC_NUM"))
        if not code:
            continue
        if not include_deprecated and is_deprecated_loinc(row):
            continue
        terms[code] = row
        if limit is not None and len(terms) >= limit:
            break
    return terms


def load_consumer_names(loinc_dir: Path, codes: set[str]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = defaultdict(list)
    path = loinc_dir / "AccessoryFiles" / "ConsumerName" / "ConsumerName.csv"
    for row in optional_csv(path):
        code = clean(row.get("LoincNumber"))
        if code in codes:
            names[code].append(clean(row.get("ConsumerName")))
    return {code: unique_texts(values) for code, values in names.items()}


def load_part_links(loinc_dir: Path, codes: set[str]) -> dict[str, list[dict[str, str]]]:
    links: dict[str, list[dict[str, str]]] = defaultdict(list)
    base = loinc_dir / "AccessoryFiles" / "PartFile"
    for filename in ("LoincPartLink_Primary.csv", "LoincPartLink_Supplementary.csv"):
        for row in optional_csv(base / filename):
            code = clean(row.get("LoincNumber"))
            if code not in codes:
                continue
            links[code].append(
                {
                    "partNumber": clean(row.get("PartNumber")),
                    "partName": clean(row.get("PartName")),
                    "partCodeSystem": clean(row.get("PartCodeSystem")),
                    "partTypeName": clean(row.get("PartTypeName")),
                    "linkTypeName": clean(row.get("LinkTypeName")),
                    "property": clean(row.get("Property")),
                }
            )
    return links


def load_groups(loinc_dir: Path, codes: set[str]) -> dict[str, list[dict[str, str]]]:
    group_rows: dict[str, dict[str, str]] = {}
    base = loinc_dir / "AccessoryFiles" / "GroupFile"
    for row in optional_csv(base / "Group.csv"):
        group_id = clean(row.get("GroupId"))
        if group_id:
            group_rows[group_id] = row

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in optional_csv(base / "GroupLoincTerms.csv"):
        code = clean(row.get("LoincNumber"))
        if code not in codes:
            continue
        group_id = clean(row.get("GroupId"))
        group = group_rows.get(group_id, {})
        groups[code].append(
            {
                "category": clean(row.get("Category")),
                "groupId": group_id,
                "group": clean(group.get("Group")),
                "archetype": clean(row.get("Archetype") or group.get("Archetype")),
                "parentGroupId": clean(group.get("ParentGroupId")),
                "status": clean(group.get("Status")),
            }
        )
    return groups


def load_panels(
    loinc_dir: Path,
    codes: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[dict[str, str]]]]:
    parents: dict[str, list[str]] = defaultdict(list)
    children: dict[str, list[str]] = defaultdict(list)
    rows_by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    path = loinc_dir / "AccessoryFiles" / "PanelsAndForms" / "PanelsAndForms.csv"
    for row in optional_csv(path):
        code = clean(row.get("Loinc"))
        parent_code = clean(row.get("ParentLoinc"))
        if code in codes:
            rows_by_code[code].append(row)
        if not code or not parent_code or code == parent_code:
            continue
        if code in codes:
            parents[code].append(parent_code)
        if parent_code in codes:
            children[parent_code].append(code)
    return (
        {code: unique_texts(values) for code, values in parents.items()},
        {code: unique_texts(values) for code, values in children.items()},
        rows_by_code,
    )


def load_maps(loinc_dir: Path, codes: set[str]) -> dict[str, list[dict[str, str]]]:
    maps: dict[str, list[dict[str, str]]] = defaultdict(list)
    path = loinc_dir / "LoincTable" / "MapTo.csv"
    for row in optional_csv(path):
        code = clean(row.get("LOINC"))
        if code not in codes:
            continue
        maps[code].append(
            {
                "mapTo": clean(row.get("MAP_TO")),
                "comment": clean(row.get("COMMENT")),
            }
        )
    return maps


def load_answer_lists(loinc_dir: Path, codes: set[str]) -> dict[str, list[dict[str, Any]]]:
    base = loinc_dir / "AccessoryFiles" / "AnswerFile"
    answer_rows_by_list: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in optional_csv(base / "AnswerList.csv"):
        answer_list_id = clean(row.get("AnswerListId"))
        if answer_list_id:
            answer_rows_by_list[answer_list_id].append(row)

    answer_lists: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in optional_csv(base / "LoincAnswerListLink.csv"):
        code = clean(row.get("LoincNumber"))
        if code not in codes:
            continue
        answer_list_id = clean(row.get("AnswerListId"))
        answers = [
            {
                "answerStringId": clean(answer.get("AnswerStringId")),
                "localAnswerCode": clean(answer.get("LocalAnswerCode")),
                "sequenceNumber": clean(answer.get("SequenceNumber")),
                "displayText": clean(answer.get("DisplayText")),
                "description": clean(answer.get("Description")),
                "score": clean(answer.get("Score")),
            }
            for answer in answer_rows_by_list.get(answer_list_id, [])
        ]
        answer_lists[code].append(
            {
                "answerListId": answer_list_id,
                "answerListName": clean(row.get("AnswerListName")),
                "answerListLinkType": clean(row.get("AnswerListLinkType")),
                "applicableContext": clean(row.get("ApplicableContext")),
                "answers": answers,
            }
        )
    return answer_lists


def variant_key_from_path(path: Path) -> str:
    match = re.match(r"([a-z]{2})([A-Z]{2})", path.name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return path.stem


def load_linguistic_variants(loinc_dir: Path, codes: set[str]) -> dict[str, list[dict[str, str]]]:
    variants: dict[str, list[dict[str, str]]] = defaultdict(list)
    base = loinc_dir / "AccessoryFiles" / "LinguisticVariants"
    for path in sorted(base.glob("*LinguisticVariant.csv")):
        language = variant_key_from_path(path)
        for row in optional_csv(path):
            code = clean(row.get("LOINC_NUM"))
            if code not in codes:
                continue
            terms = {
                "component": clean(row.get("COMPONENT")),
                "property": clean(row.get("PROPERTY")),
                "timeAspect": clean(row.get("TIME_ASPCT")),
                "system": clean(row.get("SYSTEM")),
                "scaleType": clean(row.get("SCALE_TYP")),
                "methodType": clean(row.get("METHOD_TYP")),
                "class": clean(row.get("CLASS")),
                "shortName": clean(row.get("SHORTNAME")),
                "longCommonName": clean(row.get("LONG_COMMON_NAME")),
                "relatedNames": clean(row.get("RELATEDNAMES2")),
                "displayName": clean(row.get("LinguisticVariantDisplayName")),
            }
            if any(terms.values()):
                variants[code].append({"language": language, **terms})
    return variants


def compute_ancestors(parents_by_code: dict[str, list[str]], codes: set[str]) -> dict[str, list[str]]:
    ancestors: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def visit(code: str) -> list[str]:
        if code in ancestors:
            return ancestors[code]
        if code in visiting:
            return []
        visiting.add(code)
        output: list[str] = []
        for parent in parents_by_code.get(code, []):
            if parent not in codes:
                continue
            output.append(parent)
            output.extend(visit(parent))
        visiting.remove(code)
        ancestors[code] = unique_texts(output)
        return ancestors[code]

    for code in codes:
        visit(code)
    return ancestors


def build_descriptions(
    row: dict[str, str],
    *,
    consumer_names: list[str],
    variants: list[dict[str, str]],
) -> list[dict[str, str]]:
    descriptions = [
        {
            "type": "long_common_name",
            "term": clean(row.get("LONG_COMMON_NAME")),
            "languageCode": "en",
            "acceptability": "preferred",
        },
        {
            "type": "short_name",
            "term": clean(row.get("SHORTNAME")),
            "languageCode": "en",
            "acceptability": "acceptable",
        },
        {
            "type": "display_name",
            "term": clean(row.get("DisplayName")),
            "languageCode": "en",
            "acceptability": "acceptable",
        },
        {
            "type": "consumer_name",
            "term": clean(row.get("CONSUMER_NAME")),
            "languageCode": "en",
            "acceptability": "acceptable",
        },
    ]
    descriptions.extend(
        {
            "type": "consumer_name",
            "term": name,
            "languageCode": "en",
            "acceptability": "acceptable",
        }
        for name in consumer_names
    )
    for name in split_related_names(clean(row.get("RELATEDNAMES2"))):
        descriptions.append(
            {
                "type": "related_name",
                "term": name,
                "languageCode": "en",
                "acceptability": "acceptable",
            }
        )
    for variant in variants:
        language = variant["language"]
        for field, description_type in (
            ("longCommonName", "long_common_name"),
            ("shortName", "short_name"),
            ("displayName", "display_name"),
            ("relatedNames", "related_name"),
            ("component", "component"),
        ):
            term = clean(variant.get(field))
            if term:
                descriptions.append(
                    {
                        "type": description_type,
                        "term": term,
                        "languageCode": language,
                        "acceptability": "acceptable",
                    }
                )
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, str]] = []
    for item in descriptions:
        term = clean(item.get("term"))
        if not term:
            continue
        key = (item["type"], item["languageCode"], term.casefold())
        if key in seen:
            continue
        seen.add(key)
        item["term"] = term
        output.append(item)
    return output


def build_search_text(*values: Any) -> str:
    flattened: list[Any] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, list | tuple | set):
            for item in value:
                add(item)
        else:
            flattened.append(value)

    for value in values:
        add(value)
    return "\n".join(clean_loinc_search_terms(flattened))


def build_documents(
    terms: dict[str, dict[str, str]],
    *,
    consumer_names: dict[str, list[str]],
    part_links: dict[str, list[dict[str, str]]],
    groups: dict[str, list[dict[str, str]]],
    parent_codes: dict[str, list[str]],
    child_codes: dict[str, list[str]],
    ancestor_codes: dict[str, list[str]],
    panel_rows: dict[str, list[dict[str, str]]],
    maps: dict[str, list[dict[str, str]]],
    answer_lists: dict[str, list[dict[str, Any]]],
    variants: dict[str, list[dict[str, str]]],
) -> Iterable[dict[str, Any]]:
    for code, row in terms.items():
        concept_id = loinc_code_to_id(code)
        parent_ids = [loinc_code_to_id(item) for item in parent_codes.get(code, [])]
        child_ids = [loinc_code_to_id(item) for item in child_codes.get(code, [])]
        ancestor_ids = [loinc_code_to_id(item) for item in ancestor_codes.get(code, [])]
        preferred_term = (
            clean(row.get("LONG_COMMON_NAME"))
            or clean(row.get("SHORTNAME"))
            or clean(row.get("COMPONENT"))
            or code
        )
        fsn = f"{preferred_term} (LOINC)"
        synonyms = unique_texts(
            [
                clean(row.get("SHORTNAME")),
                clean(row.get("DisplayName")),
                clean(row.get("CONSUMER_NAME")),
                clean(row.get("COMPONENT")),
                *consumer_names.get(code, []),
                *split_related_names(clean(row.get("RELATEDNAMES2"))),
            ]
        )
        definitions = unique_texts(
            [
                clean(row.get("DefinitionDescription")),
            ]
        )
        attributes = [
            {"name": field, "value": clean(row.get(field))}
            for field in (
                "COMPONENT",
                "PROPERTY",
                "TIME_ASPCT",
                "SYSTEM",
                "SCALE_TYP",
                "METHOD_TYP",
                "CLASS",
                "ORDER_OBS",
                "CLASSTYPE",
                "UNITSREQUIRED",
                "EXAMPLE_UNITS",
                "EXAMPLE_UCUM_UNITS",
                "COMMON_TEST_RANK",
                "COMMON_ORDER_RANK",
                "PanelType",
            )
            if clean(row.get(field))
        ]
        attributes.extend({"name": "part", **part} for part in part_links.get(code, []))
        relationships = []
        relationships.extend(
            {
                "type": "panel-parent",
                "targetCode": parent,
                "targetConceptId": loinc_code_to_id(parent),
                "targetTerm": clean(terms.get(parent, {}).get("LONG_COMMON_NAME")),
            }
            for parent in parent_codes.get(code, [])
            if parent in terms
        )
        relationships.extend(
            {
                "type": "panel-child",
                "targetCode": child,
                "targetConceptId": loinc_code_to_id(child),
                "targetTerm": clean(terms.get(child, {}).get("LONG_COMMON_NAME")),
            }
            for child in child_codes.get(code, [])
            if child in terms
        )
        relationships.extend(
            {
                "type": "map-to",
                "targetCode": item["mapTo"],
                "comment": item["comment"],
            }
            for item in maps.get(code, [])
        )
        maps_payload = {
            "mapTo": maps.get(code, []),
            "answerLists": answer_lists.get(code, []),
        }
        descriptions = build_descriptions(
            row,
            consumer_names=consumer_names.get(code, []),
            variants=variants.get(code, []),
        )
        search_text = build_search_text(
            [fsn, preferred_term],
            [row.get(field) for field in TEXT_FIELDS],
            synonyms,
            definitions,
            consumer_names.get(code, []),
        )
        payload = {
            "terminology": "loinc",
            "code": code,
            "loincNumber": code,
            "displayCode": code,
            "status": clean(row.get("STATUS")),
            "versionLastChanged": clean(row.get("VersionLastChanged")),
            "versionFirstReleased": clean(row.get("VersionFirstReleased")),
            "row": row,
            "panelRows": panel_rows.get(code, []),
            "consumerNames": consumer_names.get(code, []),
            "linguisticVariantCount": len(variants.get(code, [])),
            "parentCodes": parent_codes.get(code, []),
            "childCodes": child_codes.get(code, []),
            "ancestorCodes": ancestor_codes.get(code, []),
        }
        yield {
            "concept_id": concept_id,
            "active": is_active_loinc(row),
            "effective_time": version_to_int(row.get("VersionLastChanged", "")),
            "module_id": LOINC_MODULE_ID,
            "definition_status_id": LOINC_DEFINITION_STATUS_ID,
            "definition_status": clean(row.get("STATUS")),
            "fsn": fsn,
            "preferred_term": preferred_term,
            "semantic_tag": semantic_tag(row),
            "synonyms": synonyms,
            "text_definitions": definitions,
            "parent_ids": parent_ids,
            "ancestor_ids": ancestor_ids,
            "child_ids": child_ids,
            "descriptions": Jsonb(descriptions),
            "relationships": Jsonb(relationships),
            "concrete_values": Jsonb([]),
            "maps": Jsonb(maps_payload),
            "associations": Jsonb(groups.get(code, [])),
            "refset_ids": [],
            "attributes": Jsonb(attributes),
            "search_text": search_text,
            "payload": Jsonb(payload),
        }


def main() -> int:
    args = parse_args()
    start = time.perf_counter()
    loinc_dir = args.loinc_dir
    if not (loinc_dir / "LoincTable" / "Loinc.csv").exists():
        raise SystemExit(f"Could not find LoincTable/Loinc.csv under {loinc_dir}")

    print(f"LOINC release: {loinc_dir}", flush=True)
    print("Loading LOINC terms", flush=True)
    terms = load_loinc_terms(
        loinc_dir,
        include_deprecated=args.include_deprecated,
        limit=args.limit,
    )
    codes = set(terms)
    print(f"Loaded {len(terms):,} terms", flush=True)

    consumer_names: dict[str, list[str]] = {}
    part_links: dict[str, list[dict[str, str]]] = {}
    groups: dict[str, list[dict[str, str]]] = {}
    parent_codes: dict[str, list[str]] = {}
    child_codes: dict[str, list[str]] = {}
    panel_rows: dict[str, list[dict[str, str]]] = {}
    maps: dict[str, list[dict[str, str]]] = {}
    answer_lists: dict[str, list[dict[str, Any]]] = {}
    variants: dict[str, list[dict[str, str]]] = {}

    if not args.skip_optional:
        print("Loading LOINC optional enrichments", flush=True)
        consumer_names = load_consumer_names(loinc_dir, codes)
        part_links = load_part_links(loinc_dir, codes)
        groups = load_groups(loinc_dir, codes)
        parent_codes, child_codes, panel_rows = load_panels(loinc_dir, codes)
        maps = load_maps(loinc_dir, codes)
        answer_lists = load_answer_lists(loinc_dir, codes)
        if not args.skip_linguistic_variants:
            variants = load_linguistic_variants(loinc_dir, codes)

    print("Precomputing panel ancestor arrays", flush=True)
    ancestor_codes = compute_ancestors(parent_codes, codes)

    config.set_database_url(args.database_url)
    concept_table = concept_table_name(DEFAULT_TERMINOLOGY, args.version)
    print(f"Connecting to Postgres: {args.database_url}", flush=True)
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
            terminology_key=DEFAULT_TERMINOLOGY,
            terminology_version=args.version,
            set_default_version=args.default_version,
            base_version_key=args.base_version,
            package_key=args.package_key,
            package_version=args.package_version,
            package_type=args.package_type,
        )
        upsert_sql = upsert_sql_for_table(concept_table)
        document_iter = build_documents(
            terms,
            consumer_names=consumer_names,
            part_links=part_links,
            groups=groups,
            parent_codes=parent_codes,
            child_codes=child_codes,
            ancestor_codes=ancestor_codes,
            panel_rows=panel_rows,
            maps=maps,
            answer_lists=answer_lists,
            variants=variants,
        )
        total = 0
        for batch in batched(document_iter, args.batch_size):
            with conn.cursor() as cur:
                cur.executemany(upsert_sql, batch)
            conn.commit()
            total += len(batch)
            elapsed = max(time.perf_counter() - start, 1e-9)
            print(f"Upserted {total:,}/{len(terms):,} terms ({total / elapsed:.1f}/s)", flush=True)

    print(f"Done in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
