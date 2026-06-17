"""Command line entry points for local development and maintenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from flat_searcher.config import AppConfig
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.read_repository import ListingReadRepository
from flat_searcher.db.repository import open_database
from flat_searcher.filtering import ListingFilters
from flat_searcher.logging_config import configure_logging
from flat_searcher.mapping import build_map_markers
from flat_searcher.presentation import detail_view_model, ranking_row_view_model
from flat_searcher.ranking import rank_candidates
from flat_searcher.scraper.http_client import HttpTextClient
from flat_searcher.geo.geocoder import NominatimGeocoder
from flat_searcher.services.geocoding import GeocodingService
from flat_searcher.services.sync import ListingSyncService
from flat_searcher.ui import DesktopUIConfig, UIDependencyError, run_desktop_app


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

    sync_parser = subparsers.add_parser("sync-listings", help="Fetch SS.com listings into SQLite.")
    sync_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    sync_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit detail page fetches for development runs.",
    )
    sync_parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Delay between HTTP requests in seconds.",
    )
    sync_parser.add_argument(
        "--mark-missing-inactive",
        action="store_true",
        help="Mark active listings missing from this run as inactive. Use only for full syncs.",
    )

    ranking_parser = subparsers.add_parser("show-ranking", help="Print ranked listings from SQLite.")
    ranking_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    ranking_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    ranking_parser.add_argument("--limit", type=int, default=20, help="Maximum rows to print.")
    ranking_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive listings.",
    )
    ranking_parser.add_argument(
        "--show-rejected",
        action="store_true",
        help="Include rejected listings.",
    )
    ranking_parser.add_argument(
        "--favorites-only",
        action="store_true",
        help="Show only favorite listings.",
    )

    detail_parser = subparsers.add_parser("show-detail", help="Print one listing detail view model.")
    detail_parser.add_argument(
        "listing_id",
        type=int,
        help="Internal listing database ID.",
    )
    detail_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    detail_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )

    ui_parser = subparsers.add_parser("run-ui", help="Start the desktop UI.")
    ui_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    ui_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )

    map_parser = subparsers.add_parser("show-map-markers", help="Print map marker JSON.")
    map_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    map_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    map_parser.add_argument("--limit", type=int, default=None, help="Maximum markers to print.")

    geocode_parser = subparsers.add_parser("geocode-listings", help="Geocode listings missing coordinates.")
    geocode_parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )
    geocode_parser.add_argument("--limit", type=int, default=None, help="Maximum listings to geocode.")

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

    if args.command == "sync-listings":
        service = ListingSyncService(
            database_path=config.database_path,
            start_url=config.ss_start_url,
            http_client=HttpTextClient(request_delay_seconds=args.request_delay),
        )
        result = service.sync(
            limit=args.limit,
            mark_missing_inactive=args.mark_missing_inactive and args.limit is None,
        )
        print(f"Seen listings: {result.seen_count}")
        print(f"Stored listings: {result.stored_count}")
        print(f"New listings: {result.new_count}")
        print(f"Changed listings: {result.changed_count}")
        print(f"Marked inactive: {result.inactive_count}")
        print(f"Detail fetch failures: {result.failed_count}")
        return 0

    if args.command == "show-ranking":
        init_database(config.database_path)
        with open_database(config.database_path) as connection:
            read_repository = ListingReadRepository(connection)
            candidates = read_repository.load_candidates(args.profile)
            ranked = rank_candidates(
                candidates,
                ListingFilters(
                    show_inactive=args.include_inactive,
                    hide_rejected=not args.show_rejected,
                    favorites_only=args.favorites_only,
                ),
            )
        if not ranked:
            print("No listings match the selected filters.")
            return 0
        for row in ranked[: args.limit]:
            view_model = ranking_row_view_model(row)
            print(f"#{view_model.position} - Score {view_model.score_text} - {view_model.title}")
        return 0

    if args.command == "show-detail":
        init_database(config.database_path)
        with open_database(config.database_path) as connection:
            detail = ListingReadRepository(connection).load_detail(args.listing_id, args.profile)
        if detail is None:
            print(f"Listing not found: {args.listing_id}")
            return 1
        view_model = detail_view_model(detail)
        print(view_model.title)
        print(f"Original listing: {view_model.ss_url}")
        _print_section("Top", view_model.top_lines)
        _print_section("Layout", view_model.layout_lines)
        _print_section("Mortgage", view_model.mortgage_lines)
        _print_section("Location", view_model.location_lines)
        _print_section("History", view_model.history_lines)
        return 0

    if args.command == "run-ui":
        try:
            return run_desktop_app(
                DesktopUIConfig(database_path=config.database_path, profile_key=args.profile)
            )
        except UIDependencyError as error:
            print(str(error))
            return 1

    if args.command == "show-map-markers":
        init_database(config.database_path)
        with open_database(config.database_path) as connection:
            points = ListingReadRepository(connection).load_map_points(args.profile)
        markers = build_map_markers(points)
        if args.limit is not None:
            markers = markers[: args.limit]
        print(json.dumps([marker.to_dict() for marker in markers], ensure_ascii=False, indent=2))
        return 0

    if args.command == "geocode-listings":
        init_database(config.database_path)
        result = GeocodingService(
            database_path=config.database_path,
            geocoder=NominatimGeocoder(),
        ).geocode_missing(limit=args.limit)
        print(f"Checked listings: {result.checked_count}")
        print(f"Geocoded listings: {result.geocoded_count}")
        print(f"Location scores enabled: {result.score_enabled_count}")
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


def _print_section(title: str, lines: tuple[str, ...]) -> None:
    print("")
    print(f"{title}:")
    for line in lines:
        print(f"  {line}")
