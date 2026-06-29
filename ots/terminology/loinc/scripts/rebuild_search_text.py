#!/usr/bin/env python3
"""Rebuild LOINC search_text from denormalized rows with English-only text."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql

from ots import config
from ots.db.terminology_postgres import (
    concept_table_name,
    connect_db,
)
from ots.terminology.loinc.scripts.load_loinc_postgres import (
    DEFAULT_TERMINOLOGY,
    TEXT_FIELDS,
    build_search_text,
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


def row_to_search_text(row: dict[str, Any]) -> str:
    payload = row.get("payload") or {}
    source_row = payload.get("row") or {}
    consumer_names = payload.get("consumerNames") or []
    return build_search_text(
        [row.get("fsn"), row.get("preferred_term")],
        [source_row.get(field) for field in TEXT_FIELDS],
        row.get("synonyms") or [],
        [source_row.get("DefinitionDescription")],
        consumer_names,
    )


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be greater than 0")
    config.set_database_url(args.database_url)
    concept_table = concept_table_name(DEFAULT_TERMINOLOGY)
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
        select_sql = sql.SQL(
            """
            SELECT
                concept_id,
                fsn,
                preferred_term,
                synonyms,
                text_definitions,
                payload
            FROM {concept_table}
            WHERE concept_id > %s
            ORDER BY concept_id
            LIMIT %s
            """
        ).format(concept_table=sql.Identifier(concept_table))
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
            payload = [
                (row_to_search_text(row), int(row["concept_id"])) for row in batch
            ]
            with conn.cursor() as cur:
                cur.executemany(update_sql, payload)
            conn.commit()
            updated += len(batch)
            elapsed = max(time.perf_counter() - start, 1e-9)
            print(
                f"Updated {updated:,}/{total:,} rows ({updated / elapsed:.1f}/s)",
                flush=True,
            )

        if args.clear_embeddings:
            model_rows = conn.execute(
                """
                SELECT embedding_table
                FROM embedding_model
                WHERE terminology_key = %s
                  AND embedding_table IS NOT NULL
                """,
                (DEFAULT_TERMINOLOGY,),
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
            print(
                f"Deleted {deleted:,} embedding rows for {DEFAULT_TERMINOLOGY!r}",
                flush=True,
            )

    print(f"Done in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
