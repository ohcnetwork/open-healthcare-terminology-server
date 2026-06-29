#!/usr/bin/env python3
"""Dump the Postgres terminology database for sharing or backup."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ots import config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        type=Path,
        help="Output dump path, for example data/exports/ots.dump",
    )
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL used by local pg_dump",
    )
    parser.add_argument(
        "--format",
        choices=("custom", "plain"),
        default="custom",
        help="Dump format. custom is recommended for sharing.",
    )
    parser.add_argument(
        "--compress",
        type=int,
        default=6,
        help="Compression level for custom dumps, from 0 to 9",
    )
    parser.add_argument(
        "--via-docker",
        action="store_true",
        default=True,
        help="Run pg_dump inside the docker compose Postgres service",
    )
    parser.add_argument(
        "--local",
        action="store_false",
        dest="via_docker",
        help="Use local pg_dump instead of docker compose",
    )
    parser.add_argument("--compose-file", default="docker-compose.yml")
    parser.add_argument("--service", default="postgres")
    parser.add_argument("--db-name", default="ots")
    parser.add_argument("--db-user", default="ots")
    return parser.parse_args()


def dump_command(args: argparse.Namespace) -> list[str]:
    if args.via_docker:
        command = [
            "docker",
            "compose",
            "-f",
            args.compose_file,
            "exec",
            "-T",
            args.service,
            "pg_dump",
            "-U",
            args.db_user,
            "-d",
            args.db_name,
            "--no-owner",
            "--no-acl",
        ]
    else:
        command = [
            "pg_dump",
            args.database_url,
            "--no-owner",
            "--no-acl",
        ]
    if args.format == "custom":
        command.extend(["--format=custom", f"--compress={args.compress}"])
    else:
        command.extend(["--format=plain"])
    return command


def main() -> int:
    args = parse_args()
    if args.compress < 0 or args.compress > 9:
        raise SystemExit("--compress must be between 0 and 9")

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(f"{output.suffix}.tmp")
    if temp_output.exists():
        temp_output.unlink()

    started = time.perf_counter()
    command = dump_command(args)
    print(f"Writing database dump to {output}", flush=True)
    with temp_output.open("wb") as handle:
        result = subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.PIPE,
            text=True,
        )
    if result.returncode != 0:
        if temp_output.exists():
            temp_output.unlink()
        message = result.stderr.strip() or f"Command failed with exit code {result.returncode}"
        raise SystemExit(
            "Database dump failed.\n\n"
            f"{message}\n\n"
            "This project uses Postgres 16 in Docker. If you intended to use local "
            "Postgres tools, install matching PostgreSQL 16 client tools and rerun "
            "with --local."
        )
    temp_output.replace(output)
    elapsed = time.perf_counter() - started
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Dump complete: {output} ({size_mb:.1f} MiB, {elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
