#!/usr/bin/env python3
"""Refresh a composed terminology edition from a base/core edition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ots import config
from ots.db.terminology_postgres import (
    DEFAULT_TERMINOLOGY_KEY,
    resync_terminology_edition,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=config.DATABASE_URL)
    parser.add_argument(
        "--terminology",
        default=config.TERMINOLOGY_KEY,
        help=f"Terminology key (default: {DEFAULT_TERMINOLOGY_KEY})",
    )
    parser.add_argument(
        "--source-version",
        required=True,
        help="Base/core edition version to copy from",
    )
    parser.add_argument(
        "--target-version",
        required=True,
        help="Composed edition version to refresh",
    )
    parser.add_argument(
        "--keep-inherited",
        action="store_true",
        help="Do not clear previously inherited rows before copying from the source",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config.set_database_url(args.database_url)
    result = resync_terminology_edition(
        terminology_key=args.terminology,
        source_version=args.source_version,
        target_version=args.target_version,
        clear_inherited=not args.keep_inherited,
    )
    print(
        "Resynced {terminology} {targetVersion} from {sourceVersion}: "
        "deleted {deletedInheritedRows:,} inherited rows, copied/updated "
        "{copiedOrUpdatedRows:,} rows".format(**result),
        flush=True,
    )
    print(f"Target table: {result['targetConceptTable']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
