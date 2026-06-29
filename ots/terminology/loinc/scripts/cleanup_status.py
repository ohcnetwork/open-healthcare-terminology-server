#!/usr/bin/env python3
"""Mark discouraged/deprecated LOINC rows inactive without touching embeddings."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql

from ots import config
from ots.db.terminology_postgres import concept_table_name, connect_db
from ots.terminology.loinc.scripts.load_loinc_postgres import DEFAULT_TERMINOLOGY

INACTIVE_STATUSES = ("DEPRECATED", "DISCOURAGED")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config.set_database_url(args.database_url)
    concept_table = concept_table_name(DEFAULT_TERMINOLOGY)
    started = time.perf_counter()
    with connect_db() as conn:
        inactive = conn.execute(
            sql.SQL(
                """
                UPDATE {concept_table}
                SET active = false,
                    updated_at = now()
                WHERE COALESCE(payload->>'status', payload->'row'->>'STATUS', definition_status) = ANY(%s::text[])
                  AND active IS DISTINCT FROM false
                """
            ).format(concept_table=sql.Identifier(concept_table)),
            (list(INACTIVE_STATUSES),),
        )
        active = conn.execute(
            sql.SQL(
                """
                UPDATE {concept_table}
                SET active = true,
                    updated_at = now()
                WHERE COALESCE(payload->>'status', payload->'row'->>'STATUS', definition_status) <> ALL(%s::text[])
                  AND active IS DISTINCT FROM true
                """
            ).format(concept_table=sql.Identifier(concept_table)),
            (list(INACTIVE_STATUSES),),
        )
        summary = conn.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE active) AS active,
                    COUNT(*) FILTER (
                        WHERE COALESCE(payload->>'status', payload->'row'->>'STATUS', definition_status) = 'DISCOURAGED'
                    ) AS discouraged,
                    COUNT(*) FILTER (
                        WHERE COALESCE(payload->>'status', payload->'row'->>'STATUS', definition_status) = 'DISCOURAGED'
                          AND active
                    ) AS active_discouraged
                FROM {concept_table}
                """
            ).format(concept_table=sql.Identifier(concept_table))
        ).fetchone()
        conn.commit()

    print(
        "Updated "
        f"{int(inactive.rowcount or 0):,} rows inactive and "
        f"{int(active.rowcount or 0):,} rows active",
        flush=True,
    )
    print(
        "Summary: "
        f"{int(summary['active']):,}/{int(summary['total']):,} active; "
        f"{int(summary['discouraged']):,} discouraged; "
        f"{int(summary['active_discouraged']):,} active discouraged",
        flush=True,
    )
    print(f"Done in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
