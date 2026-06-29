#!/usr/bin/env python3
"""Backfill display LOINC codes without touching embeddings."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from psycopg.types.json import Jsonb

from ots import config
from ots.db.terminology_postgres import concept_table_name, connect_db
from ots.terminology.loinc.scripts.load_loinc_postgres import DEFAULT_TERMINOLOGY


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument("--limit", type=int)
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


def clean_code(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def display_loinc_code(value: Any) -> str | None:
    text = clean_code(value)
    if not text:
        return None
    if re.fullmatch(r"\d+-\d", text):
        return text
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) > 1:
        return f"{digits[:-1]}-{digits[-1]}"
    return text


def payload_loinc_code(payload: dict[str, Any]) -> str | None:
    row = payload.get("row")
    if isinstance(row, dict):
        code = display_loinc_code(row.get("LOINC_NUM"))
        if code:
            return code
    for key in ("code", "loincNumber", "displayCode"):
        code = display_loinc_code(payload.get(key))
        if code:
            return code
    return None


def cleaned_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    code = payload_loinc_code(payload)
    if not code:
        return payload, False
    updated = dict(payload)
    updated["code"] = code
    updated["loincNumber"] = code
    updated["displayCode"] = code
    row = updated.get("row")
    if isinstance(row, dict):
        updated_row = dict(row)
        updated_row["LOINC_NUM"] = code
        updated["row"] = updated_row
    return updated, updated != payload


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be greater than 0")
    config.set_database_url(args.database_url)
    concept_table = concept_table_name(DEFAULT_TERMINOLOGY)
    started = time.perf_counter()
    updated = 0
    scanned = 0
    last_concept_id = 0

    with connect_db() as conn:
        select_sql = sql.SQL(
            """
            SELECT concept_id, payload
            FROM {concept_table}
            WHERE concept_id > %s
            ORDER BY concept_id
            LIMIT %s
            """
        ).format(concept_table=sql.Identifier(concept_table))
        update_sql = sql.SQL(
            """
            UPDATE {concept_table}
            SET payload = %s,
                updated_at = now()
            WHERE concept_id = %s
            """
        ).format(concept_table=sql.Identifier(concept_table))
        while args.limit is None or scanned < args.limit:
            remaining = (
                args.limit - scanned if args.limit is not None else args.batch_size
            )
            limit = min(args.batch_size, remaining)
            rows = conn.execute(select_sql, (last_concept_id, limit)).fetchall()
            if not rows:
                break
            batch = [dict(row) for row in rows]
            last_concept_id = int(batch[-1]["concept_id"])
            scanned += len(batch)
            payload_updates: list[tuple[Jsonb, int]] = []
            for row in batch:
                payload = row.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                payload, changed = cleaned_payload(payload)
                if changed:
                    payload_updates.append((Jsonb(payload), int(row["concept_id"])))
            if payload_updates:
                with conn.cursor() as cur:
                    cur.executemany(update_sql, payload_updates)
                conn.commit()
                updated += len(payload_updates)
            else:
                conn.commit()
            elapsed = max(time.perf_counter() - started, 1e-9)
            print(
                f"Scanned {scanned:,} rows; cleaned {updated:,} "
                f"({scanned / elapsed:.1f}/s)",
                flush=True,
            )

    print(f"Done in {time.perf_counter() - started:.1f}s; cleaned {updated:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
