#!/usr/bin/env python3
"""Load a grouped set of SNOMED CT RF2 packages into one terminology edition."""

from __future__ import annotations

import argparse
import json
import sys
import time
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql

from ots import config
from ots.db.terminology_postgres import (
    connect_db,
    concept_table_name,
    resync_terminology_edition,
)
from ots.terminology.snomed.scripts.load_snomed_postgres import load_snomed_rf2_package
from ots.terminology.snomed.model import TERMINOLOGY
from ots.terminology.snomed.scripts.rf2_packages import (
    discover_snomed_rf2_packages,
    ensure_extracted_package,
    format_package_plan,
    selected_packages,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--extract-dir",
        type=Path,
        default=Path("data/imports/snomed_rf2"),
        help="Workspace for extracted RF2 zip packages",
    )
    parser.add_argument("--database-url", default=config.DATABASE_URL)
    parser.add_argument(
        "--edition-version",
        required=True,
        help="Target edition/version key, for example snomed_india_20260313",
    )
    parser.add_argument(
        "--base-version",
        help="Base edition to copy into the target before loading extension packages",
    )
    parser.add_argument(
        "--default-version",
        action="store_true",
        help="Mark the target edition/version as default",
    )
    parser.add_argument(
        "--include-package",
        action="append",
        default=[],
        help="Only include packages whose key, group, or filename contains this text",
    )
    parser.add_argument(
        "--exclude-package",
        action="append",
        default=[],
        help="Skip packages whose key, group, or filename contains this text",
    )
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--skip-optional", action="store_true")
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=config.EMBEDDING_DIMENSIONS,
    )
    parser.add_argument(
        "--recreate-edition",
        action="store_true",
        help="Drop the target edition table before resync/import",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Re-extract zip packages even when an extracted Snapshot already exists",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Only print the grouped package import plan",
    )
    return parser.parse_args()


def drop_target_table(*, terminology: str, edition_version: str) -> None:
    table_name = concept_table_name(terminology, edition_version)
    with connect_db() as conn:
        conn.execute(
            sql.SQL("DROP TABLE IF EXISTS {table_name} CASCADE").format(
                table_name=sql.Identifier(table_name)
            )
        )
        conn.commit()
    print(f"Dropped target table: {table_name}", flush=True)


def load_package(
    *,
    args: argparse.Namespace,
    rf2_root: Path,
    package,
    recreate: bool,
) -> dict:
    metadata = dict(package.metadata)
    metadata["groupedImport"] = True
    metadata["editionVersion"] = args.edition_version
    namespace = Namespace(
        rf2_dir=rf2_root,
        database_url=args.database_url,
        terminology=TERMINOLOGY.key,
        version=args.edition_version,
        base_version=args.base_version,
        package_key=package.package_key,
        package_version=package.package_version,
        package_type=package.package_type,
        package_role="extension" if args.base_version else "primary",
        package_source_uri=package.source_uri,
        package_metadata_json=json.dumps(metadata),
        edition_type="composed" if args.base_version else "standalone",
        default_version=args.default_version,
        embedding_dimensions=args.embedding_dimensions,
        batch_size=args.batch_size,
        limit=args.limit,
        include_inactive=args.include_inactive,
        skip_optional=args.skip_optional,
        recreate=recreate,
    )
    return load_snomed_rf2_package(namespace)


def main() -> int:
    args = parse_args()
    config.set_database_url(args.database_url)
    packages = selected_packages(
        discover_snomed_rf2_packages(args.source_dir),
        include=args.include_package,
        exclude=args.exclude_package,
    )
    if not packages:
        print(f"No SNOMED RF2 packages found under {args.source_dir}", file=sys.stderr)
        return 1

    print(format_package_plan(packages), flush=True)
    if args.plan_only:
        return 0

    start = time.perf_counter()
    if args.recreate_edition:
        drop_target_table(terminology=TERMINOLOGY.key, edition_version=args.edition_version)

    if args.base_version:
        result = resync_terminology_edition(
            terminology_key=TERMINOLOGY.key,
            source_version=args.base_version,
            target_version=args.edition_version,
            clear_inherited=True,
        )
        print(
            "Resynced {targetVersion} from {sourceVersion}: "
            "{copiedOrUpdatedRows:,} copied/updated, {deletedInheritedRows:,} deleted".format(
                **result
            ),
            flush=True,
        )

    total = 0
    for index, package in enumerate(packages, start=1):
        rf2_root = ensure_extracted_package(
            package,
            extract_dir=args.extract_dir,
            force=args.force_extract,
        )
        print(
            f"[{index}/{len(packages)}] Loading {package.package_key} "
            f"({package.package_version}) from {rf2_root}",
            flush=True,
        )
        result = load_package(
            args=args,
            rf2_root=rf2_root,
            package=package,
            recreate=False,
        )
        total += int(result["upserted"])

    elapsed = time.perf_counter() - start
    print(
        f"Imported {len(packages)} package(s), upserted {total:,} concept rows in {elapsed:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
