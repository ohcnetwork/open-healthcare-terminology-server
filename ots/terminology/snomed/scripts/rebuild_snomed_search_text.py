#!/usr/bin/env python3
"""Rebuild SNOMED search_text from denormalized rows with embedding-friendly text."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql

from ots import config
from ots.db.terminology_postgres import (
    DEFAULT_DATABASE_URL,
    connect_db,
    concept_table_name,
)
from ots.terminology.snomed.model import TERMINOLOGY
from ots.terminology.snomed.scripts.load_snomed_postgres import (
    build_search_text,
    clean_search_terms,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2_000,
        help="Rows to update per transaction",
    )
    parser.add_argument("--limit", type=int, help="Limit rows for a smoke test")
    parser.add_argument(
        "--clear-embeddings",
        action="store_true",
        help="Delete embedding rows for this terminology because source text changed",
    )
    return parser.parse_args()


def batched(items: Iterable[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def row_to_search_text(row: dict[str, Any], parent_terms: dict[int, str]) -> str:
    parent_texts = [parent_terms[parent_id] for parent_id in row["parent_ids"] if parent_id in parent_terms]
    relationship_terms = [
        str(item.get("destinationTerm", ""))
        for item in (row.get("relationships") or [])[:50]
        if isinstance(item, dict)
    ]
    return build_search_text(
        concept_id=int(row["concept_id"]),
        fsn=row.get("fsn"),
        preferred_term=row.get("preferred_term"),
        tag=row.get("semantic_tag"),
        synonyms=list(row.get("synonyms") or []),
        definitions=list(row.get("text_definitions") or []),
        parent_terms=parent_texts,
        relationships=[{"destinationTerm": term} for term in relationship_terms],
        maps={},
    )


def load_parent_terms(conn, concept_table: str, parent_ids: set[int]) -> dict[int, str]:
    if not parent_ids:
        return {}
    rows = conn.execute(
        sql.SQL(
            """
            SELECT concept_id, preferred_term, fsn
            FROM {concept_table}
            WHERE concept_id = ANY(%s)
            """
        ).format(concept_table=sql.Identifier(concept_table)),
        (list(parent_ids),),
    ).fetchall()
    terms: dict[int, str] = {}
    for row in rows:
        cleaned = clean_search_terms([row.get("preferred_term"), row.get("fsn")])
        if cleaned:
            terms[int(row["concept_id"])] = cleaned[0]
    return terms


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be greater than 0")
    config.set_database_url(args.database_url)
    terminology_key = TERMINOLOGY.key
    concept_table = concept_table_name(terminology_key)
    start = time.perf_counter()

    with connect_db() as conn:
        total_row = conn.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {concept_table}").format(
                concept_table=sql.Identifier(concept_table)
            )
        ).fetchone()
        total = int(total_row["count"])
        if args.limit is not None:
            total = min(total, args.limit)

        updated = 0
        last_concept_id = 0
        update_sql = sql.SQL(
            """
            UPDATE {concept_table}
            SET search_text = %s,
                embedding = NULL,
                embedding_model = NULL,
                embedding_updated_at = NULL,
                updated_at = now()
            WHERE concept_id = %s
            """
        ).format(concept_table=sql.Identifier(concept_table))
        select_sql = sql.SQL(
            """
            SELECT
                concept_id,
                fsn,
                preferred_term,
                semantic_tag,
                synonyms,
                text_definitions,
                parent_ids,
                relationships
            FROM {concept_table}
            WHERE concept_id > %s
            ORDER BY concept_id
            LIMIT %s
            """
        ).format(concept_table=sql.Identifier(concept_table))
        while updated < total:
            remaining = total - updated
            rows = conn.execute(
                select_sql,
                (last_concept_id, min(args.batch_size, remaining)),
            ).fetchall()
            if not rows:
                break
            batch = [dict(row) for row in rows]
            last_concept_id = int(batch[-1]["concept_id"])
            parent_ids = {
                int(parent_id)
                for row in batch
                for parent_id in (row.get("parent_ids") or [])
            }
            parent_terms = load_parent_terms(conn, concept_table, parent_ids)
            payload = [
                (row_to_search_text(row, parent_terms), int(row["concept_id"]))
                for row in batch
            ]
            with conn.cursor() as cur:
                cur.executemany(update_sql, payload)
            conn.commit()
            updated += len(batch)
            elapsed = max(time.perf_counter() - start, 1e-9)
            print(f"Updated {updated:,}/{total:,} rows ({updated / elapsed:.1f}/s)", flush=True)

        if args.clear_embeddings:
            model_rows = conn.execute(
                """
                SELECT embedding_table
                FROM embedding_model
                WHERE terminology_key = %s
                  AND embedding_table IS NOT NULL
                """,
                (terminology_key,),
            ).fetchall()
            deleted = 0
            for model in model_rows:
                table_name = model.get("embedding_table")
                if not table_name:
                    continue
                cursor = conn.execute(
                    sql.SQL("DELETE FROM {table_name}").format(
                        table_name=sql.Identifier(str(table_name))
                    )
                )
                deleted += int(cursor.rowcount or 0)
            conn.commit()
            print(f"Deleted {deleted:,} embedding rows for {terminology_key!r}", flush=True)

    print(f"Done in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
