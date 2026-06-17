"""Command line entry points for local development and maintenance."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from flat_searcher.config import AppConfig
from flat_searcher.db.bootstrap import init_database
from flat_searcher.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flat-searcher",
        description="Riga apartment analyzer for SS.com listings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved runtime paths and defaults.")

    init_db_parser = subparsers.add_parser("init-db", help="Create or update the SQLite database.")
    init_db_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    config = AppConfig.from_env(database_override=getattr(args, "database", None))

    if args.command == "show-config":
        _print_config(config)
        return 0

    if args.command == "init-db":
        result = init_database(config.database_path)
        print(f"Database initialized: {result.database_path}")
        print(f"Schema version: {result.schema_version}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _print_config(config: AppConfig) -> None:
    print(f"App home: {config.app_home}")
    print(f"Database: {config.database_path}")
    print(f"Cache dir: {config.cache_dir}")
    print(f"Temporary images dir: {config.temporary_images_dir}")
    print(f"Floor plans dir: {config.floor_plans_dir}")
    print(f"SS start URL: {config.ss_start_url}")
