#!/usr/bin/env python3
"""Create lexical search indexes for registered concept tables."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql

from ots import config
from ots.db import schema as db_schema
from ots.db.terminology_postgres import connect_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
    parser.add_argument("--terminology", help="Only index one terminology key")
    parser.add_argument("--version", help="Only index one terminology version")
    parser.add_argument(
        "--non-concurrent",
        action="store_true",
        help="Use regular CREATE INDEX instead of CREATE INDEX CONCURRENTLY",
    )
    return parser.parse_args()


def concept_tables(conn, *, terminology: str | None, version: str | None) -> list[dict]:
    clauses = ["to_regclass(concept_table) IS NOT NULL"]
    params: list[str] = []
    if terminology:
        clauses.append("terminology_key = %s")
        params.append(terminology)
    if version:
        clauses.append("version_key = %s")
        params.append(version)
    return conn.execute(
        f"""
        SELECT DISTINCT terminology_key, version_key, concept_table
        FROM terminology_version
        WHERE {' AND '.join(clauses)}
        ORDER BY terminology_key, version_key
        """,
        params,
    ).fetchall()


def index_statements(*, table_name: str, suffix: str, concurrently: bool):
    mode = sql.SQL("CONCURRENTLY ") if concurrently else sql.SQL("")
    return [
        (
            f"idx_{suffix}_concept_document_active",
            sql.SQL("CREATE INDEX {mode}IF NOT EXISTS {index_name} ON {table_name}(active)"),
        ),
        (
            f"idx_{suffix}_concept_document_semantic_tag",
            sql.SQL("CREATE INDEX {mode}IF NOT EXISTS {index_name} ON {table_name}(semantic_tag)"),
        ),
        (
            f"idx_{suffix}_concept_document_search_vector",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN(search_vector)"
            ),
        ),
        (
            f"idx_{suffix}_concept_document_preferred_trgm",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN(preferred_term gin_trgm_ops)"
            ),
        ),
        (
            f"idx_{suffix}_concept_document_fsn_trgm",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN(fsn gin_trgm_ops)"
            ),
        ),
        (
            f"idx_{suffix}_concept_document_search_text_trgm",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN(search_text gin_trgm_ops)"
            ),
        ),
        (
            f"idx_{suffix}_concept_document_payload_code_trgm",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN((payload->>'code') gin_trgm_ops)"
            ),
        ),
        (
            f"idx_{suffix}_concept_document_payload_display_trgm",
            sql.SQL(
                "CREATE INDEX {mode}IF NOT EXISTS {index_name} "
                "ON {table_name} USING GIN((payload->>'displayCode') gin_trgm_ops)"
            ),
        ),
    ], mode


def main() -> int:
    args = parse_args()
    config.set_database_url(args.database_url)
    with connect_db() as conn:
        conn.autocommit = True
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        rows = concept_tables(conn, terminology=args.terminology, version=args.version)
        if not rows:
            print("No registered concept tables found.")
            return 0
        for row in rows:
            suffix = db_schema.safe_index_suffix(f"{row['terminology_key']}_{row['version_key']}")
            statements, mode = index_statements(
                table_name=row["concept_table"],
                suffix=suffix,
                concurrently=not args.non_concurrent,
            )
            print(
                f"Ensuring lexical indexes on {row['concept_table']} "
                f"({row['terminology_key']} {row['version_key']})",
                flush=True,
            )
            for index_name, statement in statements:
                conn.execute(
                    statement.format(
                        mode=mode,
                        index_name=sql.Identifier(index_name),
                        table_name=sql.Identifier(row["concept_table"]),
                    )
                )
                print(f"  ready: {index_name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
