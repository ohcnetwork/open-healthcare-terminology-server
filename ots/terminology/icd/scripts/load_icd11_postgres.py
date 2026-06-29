#!/usr/bin/env python3
"""Load ICD-11 MMS simple tabulation data into a denormalized Postgres concept table."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from psycopg.types.json import Jsonb

from ots import config
from ots.cli.common.imported_loader_utils import batched, upsert_documents
from ots.db.terminology_postgres import (
    DEFAULT_DATABASE_URL,
    DEFAULT_EMBEDDING_DIMENSIONS,
    connect_db,
    concept_table_name,
    init_schema,
)
from ots.terminology import build_search_text
from ots.terminology.icd.model import Icd11Terminology

TERMINOLOGY = Icd11Terminology()
DEFAULT_SOURCE = Path("data/raw/icd11/SimpleTabulation-ICD-11-MMS-en-2026-01.zip")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
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
    )
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--recreate", action="store_true")
    return parser.parse_args()


def clean(value: str | None) -> str:
    return " ".join(str(value or "").split())


def clean_title(value: str | None) -> str:
    text = clean(value)
    return re.sub(r"^[-\s]+", "", text)


def parse_bool(value: str | None) -> bool:
    return clean(value).casefold() == "true"


def row_identity(row: dict[str, str]) -> str:
    code = clean(row.get("Code"))
    block_id = clean(row.get("BlockId"))
    if code:
        return code
    if block_id:
        return block_id
    uri = clean(row.get("Foundation URI")) or clean(row.get("Linearization URI"))
    if uri:
        return uri
    chapter = clean(row.get("ChapterNo"))
    if chapter:
        return f"chapter-{chapter}"
    return ""


def row_uri(row: dict[str, str]) -> str:
    return clean(row.get("Foundation URI")) or clean(row.get("Linearization URI")) or row_identity(row)


def browser_url(value: str | None) -> str | None:
    text = clean(value)
    match = re.search(r'"(https://[^"]+)"', text)
    if match:
        return match.group(1)
    return text or None


def iter_tabulation_rows(source: Path) -> Iterable[dict[str, str]]:
    with zipfile.ZipFile(source) as archive:
        names = [name for name in archive.namelist() if name.endswith("SimpleTabulation-ICD-11-MMS-en.txt")]
        if not names:
            raise FileNotFoundError("Could not find SimpleTabulation-ICD-11-MMS-en.txt in ICD-11 ZIP")
        with archive.open(names[0]) as handle:
            text = (line.decode("utf-8-sig", errors="replace") for line in handle)
            reader = csv.DictReader(text, delimiter="\t")
            for row in reader:
                if row_identity(row) and clean(row.get("Title")) and clean(row.get("ClassKind")):
                    yield row


def ancestor_keys(
    key: str,
    parent_by_key: dict[str, str | None],
    cache: dict[str, list[str]],
) -> list[str]:
    if key in cache:
        return cache[key]
    ancestors: list[str] = []
    seen = {key}
    current = parent_by_key.get(key)
    while current:
        if current in seen:
            break
        seen.add(current)
        ancestors.append(current)
        if current in cache:
            ancestors.extend(cache[current])
            break
        current = parent_by_key.get(current)
    cache[key] = ancestors
    return ancestors


def build_documents(rows: list[dict[str, str]], *, terminology_key: str) -> Iterable[dict[str, Any]]:
    key_by_uri = {row_uri(row): row_identity(row) for row in rows}
    rows_by_key = {row_identity(row): row for row in rows}
    parent_by_key: dict[str, str | None] = {}
    for row in rows:
        key = row_identity(row)
        parent_uri = clean(row.get("Parent"))
        parent_by_key[key] = key_by_uri.get(parent_uri) if parent_uri else None
    child_keys: dict[str, list[str]] = defaultdict(list)
    for key, parent in parent_by_key.items():
        if parent:
            child_keys[parent].append(key)
    concept_id_by_key = {key: TERMINOLOGY.code_to_concept_id(key) for key in rows_by_key}
    ancestor_cache: dict[str, list[str]] = {}
    for key, row in rows_by_key.items():
        code = clean(row.get("Code"))
        block_id = clean(row.get("BlockId"))
        class_kind = clean(row.get("ClassKind")).lower() or "category"
        title = clean_title(row.get("Title")) or key
        primary_tabulation = clean(row.get("Primary tabulation")).casefold() == "true"
        parent = parent_by_key.get(key)
        parents = [concept_id_by_key[parent]] if parent else []
        ancestors = [concept_id_by_key[item] for item in ancestor_keys(key, parent_by_key, ancestor_cache)]
        children = sorted(child_keys.get(key, []))
        semantic_tag = "diagnosis" if class_kind == "category" else class_kind
        chapter = clean(row.get("ChapterNo"))
        payload = {
            "code": code or block_id or (f"chapter-{chapter}" if class_kind == "chapter" else key),
            "icdCode": code,
            "blockId": block_id,
            "externalId": key,
            "title": title,
            "classKind": class_kind,
            "depthInKind": clean(row.get("DepthInKind")),
            "isResidual": parse_bool(row.get("IsResidual")),
            "isLeaf": parse_bool(row.get("isLeaf")),
            "primaryTabulation": primary_tabulation,
            "chapterNo": chapter,
            "browserLink": browser_url(row.get("BrowserLink")),
            "foundationUri": clean(row.get("Foundation URI")),
            "linearizationUri": clean(row.get("Linearization URI")),
            "parentUri": clean(row.get("Parent")),
            "terminology": terminology_key,
            "source": "WHO ICD-11 MMS 2026-01 simple tabulation",
        }
        groupings = [
            clean(row.get(name))
            for name in ("Grouping1", "Grouping2", "Grouping3", "Grouping4", "Grouping5")
            if clean(row.get(name))
        ]
        coding_note = clean(row.get("CodingNote"))
        descriptions = [
            {
                "term": title,
                "type": "Title",
                "active": True,
                "languageCode": "en",
            }
        ]
        if coding_note:
            descriptions.append(
                {
                    "term": coding_note,
                    "type": "Coding note",
                    "active": True,
                    "languageCode": "en",
                }
            )
        yield {
            "concept_id": concept_id_by_key[key],
            "active": True,
            "effective_time": 202601,
            "module_id": 0,
            "definition_status_id": 1 if class_kind == "category" else 0,
            "definition_status": class_kind,
            "fsn": f"{title} (ICD-11 MMS)",
            "preferred_term": title,
            "semantic_tag": semantic_tag,
            "synonyms": [],
            "text_definitions": [coding_note] if coding_note else [],
            "parent_ids": parents,
            "ancestor_ids": ancestors,
            "child_ids": [concept_id_by_key[item] for item in children],
            "descriptions": Jsonb(descriptions),
            "relationships": Jsonb([]),
            "concrete_values": Jsonb([]),
            "maps": Jsonb({}),
            "associations": Jsonb([]),
            "refset_ids": [],
            "attributes": Jsonb(
                [
                    {"name": "code", "value": code},
                    {"name": "blockId", "value": block_id},
                    {"name": "classKind", "value": class_kind},
                    {"name": "chapterNo", "value": chapter},
                    {"name": "groupings", "value": groupings},
                    {"name": "primaryTabulation", "value": primary_tabulation},
                ]
            ),
            "search_text": build_search_text(title, coding_note, code, block_id, groupings),
            "payload": Jsonb(payload),
        }


def main() -> None:
    args = parse_args()
    config.set_database_url(args.database_url)
    terminology_key = TERMINOLOGY.key
    started = time.time()
    rows = list(iter_tabulation_rows(args.source))
    if args.limit:
        rows = rows[: args.limit]
    concept_table = concept_table_name(terminology_key, args.version)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=args.version,
            set_default_version=args.default_version,
            base_version_key=args.base_version,
            package_key=args.package_key,
            package_version=args.package_version,
            package_type=args.package_type,
            embedding_dimensions=args.embedding_dimensions,
        )
        if args.recreate:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {table_name} CASCADE").format(
                table_name=sql.Identifier(concept_table)
            ))
            init_schema(
                conn,
                terminology_key=terminology_key,
                terminology_version=args.version,
                set_default_version=args.default_version,
                base_version_key=args.base_version,
                package_key=args.package_key,
                package_version=args.package_version,
                package_type=args.package_type,
                embedding_dimensions=args.embedding_dimensions,
            )
        total = 0
        for batch in batched(
            build_documents(rows, terminology_key=terminology_key),
            args.batch_size,
        ):
            upsert_documents(conn, concept_table=concept_table, rows=batch)
            total += len(batch)
            conn.commit()
            print(f"Loaded {total:,} ICD-11 rows", flush=True)
    print(f"Done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
