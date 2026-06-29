#!/usr/bin/env python3
"""Restore a shared Postgres terminology database dump."""

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
        "dump",
        type=Path,
        help="Dump path produced by ots.cli common dump-db",
    )
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Postgres URL used by local pg_restore or psql",
    )
    parser.add_argument(
        "--format",
        choices=("custom", "plain"),
        default="custom",
        help="Dump format. Use custom for dumps produced by the default dump command.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Drop existing objects from the target database before restoring",
    )
    parser.add_argument(
        "--via-docker",
        action="store_true",
        default=True,
        help="Run restore inside the docker compose Postgres service",
    )
    parser.add_argument(
        "--local",
        action="store_false",
        dest="via_docker",
        help="Use local pg_restore/psql instead of docker compose",
    )
    parser.add_argument("--compose-file", default="docker-compose.yml")
    parser.add_argument("--service", default="postgres")
    parser.add_argument("--db-name", default="ots")
    parser.add_argument("--db-user", default="ots")
    return parser.parse_args()


def restore_command(args: argparse.Namespace) -> list[str]:
    if args.via_docker:
        if args.format == "custom":
            command = [
                "docker",
                "compose",
                "-f",
                args.compose_file,
                "exec",
                "-T",
                args.service,
                "pg_restore",
                "-U",
                args.db_user,
                "-d",
                args.db_name,
                "--no-owner",
                "--no-acl",
            ]
        else:
            command = [
                "docker",
                "compose",
                "-f",
                args.compose_file,
                "exec",
                "-T",
                args.service,
                "psql",
                "-U",
                args.db_user,
                "-d",
                args.db_name,
            ]
    elif args.format == "custom":
        command = [
            "pg_restore",
            "--dbname",
            args.database_url,
            "--no-owner",
            "--no-acl",
        ]
    else:
        command = ["psql", args.database_url]

    if args.clean and args.format == "custom":
        command.extend(["--clean", "--if-exists"])
    return command


def main() -> int:
    args = parse_args()
    dump = args.dump
    if not dump.exists():
        raise SystemExit(f"Dump file does not exist: {dump}")
    if args.clean and args.format == "plain":
        print(
            "Warning: --clean only applies to custom-format dumps. "
            "Plain SQL dumps restore exactly what the file contains.",
            flush=True,
        )

    started = time.perf_counter()
    command = restore_command(args)
    print(f"Restoring database dump from {dump}", flush=True)
    with dump.open("rb") as handle:
        result = subprocess.run(
            command,
            stdin=handle,
            stderr=subprocess.PIPE,
            text=True,
        )
    if result.returncode != 0:
        message = result.stderr.strip() or f"Command failed with exit code {result.returncode}"
        raise SystemExit(
            "Database restore failed.\n\n"
            f"{message}\n\n"
            "This project uses Postgres 16 in Docker. If you intended to use local "
            "Postgres tools, install matching PostgreSQL 16 client tools and rerun "
            "with --local."
        )
    elapsed = time.perf_counter() - started
    print(f"Restore complete in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
