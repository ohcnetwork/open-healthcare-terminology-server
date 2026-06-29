#!/usr/bin/env python3
"""Load ICD-10-CM code descriptions into a denormalized Postgres concept table."""

from __future__ import annotations

import argparse
import sys
import time
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from psycopg.types.json import Jsonb

from ots import config
from ots.cli.common.imported_loader_utils import batched, upsert_documents
from ots.db.terminology_postgres import (
    concept_table_name,
    connect_db,
    init_schema,
)
from ots.terminology import build_search_text
from ots.terminology.icd.model import Icd10CmTerminology

TERMINOLOGY = Icd10CmTerminology()
DEFAULT_SOURCE = Path(
    "data/raw/icd10cm/april-1-2026-code-descriptions-tabular-order.zip"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
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


def display_code(raw_code: str) -> str:
    code = clean(raw_code).upper()
    if len(code) > 3:
        return f"{code[:3]}.{code[3:]}"
    return code


def iter_order_rows(source: Path) -> Iterable[dict[str, Any]]:
    with zipfile.ZipFile(source) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith("icd10cm_order_2026.txt")
        ]
        if not names:
            raise FileNotFoundError(
                "Could not find icd10cm_order_2026.txt in ICD-10-CM ZIP"
            )
        with archive.open(names[0]) as handle:
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.strip():
                    continue
                yield {
                    "sort_order": int(line[0:5]),
                    "raw_code": clean(line[6:13]).upper(),
                    "billable": clean(line[14:15]) == "1",
                    "short_title": clean(line[16:77]),
                    "long_title": clean(line[77:]),
                }


def parent_for_code(code: str, code_set: set[str]) -> str | None:
    for length in range(len(code) - 1, 2, -1):
        candidate = code[:length]
        if candidate in code_set:
            return candidate
    return None


def ancestor_codes(code: str, parent_by_code: dict[str, str | None]) -> list[str]:
    ancestors: list[str] = []
    current = parent_by_code.get(code)
    while current:
        ancestors.append(current)
        current = parent_by_code.get(current)
    return ancestors


def build_documents(
    rows: list[dict[str, Any]], *, terminology_key: str
) -> Iterable[dict[str, Any]]:
    code_set = {row["raw_code"] for row in rows}
    parent_by_code = {code: parent_for_code(code, code_set) for code in code_set}
    child_codes: dict[str, list[str]] = defaultdict(list)
    for code, parent in parent_by_code.items():
        if parent:
            child_codes[parent].append(code)
    concept_id_by_code = {
        code: TERMINOLOGY.code_to_concept_id(code)
        if terminology_key == TERMINOLOGY.key
        else Icd10CmTerminology().code_to_concept_id(code)
        for code in code_set
    }
    for row in rows:
        raw_code = row["raw_code"]
        code = display_code(raw_code)
        preferred_term = row["long_title"] or row["short_title"] or code
        short_title = row["short_title"] or preferred_term
        parent_ids = (
            [concept_id_by_code[parent_by_code[raw_code]]]
            if parent_by_code.get(raw_code)
            else []
        )
        ancestor_ids = [
            concept_id_by_code[item]
            for item in ancestor_codes(raw_code, parent_by_code)
        ]
        children = sorted(child_codes.get(raw_code, []))
        synonyms = (
            [short_title] if short_title.casefold() != preferred_term.casefold() else []
        )
        descriptions = [
            {
                "term": preferred_term,
                "type": "Long title",
                "active": True,
                "languageCode": "en",
            }
        ]
        if synonyms:
            descriptions.append(
                {
                    "term": short_title,
                    "type": "Short title",
                    "active": True,
                    "languageCode": "en",
                }
            )
        payload = {
            "code": code,
            "rawCode": raw_code,
            "displayCode": code,
            "preferredTerm": preferred_term,
            "shortTitle": short_title,
            "billable": row["billable"],
            "sortOrder": row["sort_order"],
            "terminology": terminology_key,
            "source": "CMS ICD-10-CM April 1 2026 code descriptions",
        }
        yield {
            "concept_id": concept_id_by_code[raw_code],
            "active": True,
            "effective_time": 20260401,
            "module_id": 0,
            "definition_status_id": 1 if row["billable"] else 0,
            "definition_status": "billable" if row["billable"] else "category",
            "fsn": f"{preferred_term} (ICD-10-CM)",
            "preferred_term": preferred_term,
            "semantic_tag": "diagnosis",
            "synonyms": synonyms,
            "text_definitions": [],
            "parent_ids": parent_ids,
            "ancestor_ids": ancestor_ids,
            "child_ids": [concept_id_by_code[item] for item in children],
            "descriptions": Jsonb(descriptions),
            "relationships": Jsonb([]),
            "concrete_values": Jsonb([]),
            "maps": Jsonb({}),
            "associations": Jsonb([]),
            "refset_ids": [],
            "attributes": Jsonb(
                [
                    {"name": "code", "value": code},
                    {"name": "rawCode", "value": raw_code},
                    {"name": "billable", "value": row["billable"]},
                ]
            ),
            "search_text": build_search_text(
                preferred_term, short_title, code, raw_code
            ),
            "payload": Jsonb(payload),
        }


def main() -> None:
    args = parse_args()
    config.set_database_url(args.database_url)
    terminology_key = TERMINOLOGY.key
    started = time.time()
    rows = list(iter_order_rows(args.source))
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
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {table_name} CASCADE").format(
                    table_name=sql.Identifier(concept_table)
                )
            )
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
            print(f"Loaded {total:,} ICD-10-CM rows", flush=True)
    print(f"Done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
