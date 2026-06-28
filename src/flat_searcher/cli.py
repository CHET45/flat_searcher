"""Command line entry points for local development and maintenance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from flat_searcher.ai import AIAnalysisPipeline, GeminiModelClient, GeminiSetupError
from flat_searcher.config import AppConfig
from flat_searcher.db.bootstrap import init_database
from flat_searcher.db.layout_prior_repository import LayoutPriorRepository
from flat_searcher.db.profile_repository import ProfileRepository
from flat_searcher.db.read_repository import ListingReadRepository
from flat_searcher.db.repository import open_database
from flat_searcher.filtering import ListingFilters
from flat_searcher.logging_config import configure_logging
from flat_searcher.mapping import build_map_markers
from flat_searcher.images import ImageDownloader
from flat_searcher.presentation import detail_view_model, ranking_row_view_model
from flat_searcher.ranking import rank_candidates
from flat_searcher.scraper.http_client import HttpTextClient
from flat_searcher.geo.geocoder import NominatimGeocoder
from flat_searcher.geo.overpass import OverpassPOIProvider
from flat_searcher.services.ai_analysis import (
    AIAnalysisService,
    AIAnalysisProvider,
    JsonAIAnalysisProvider,
    MockAIAnalysisProvider,
)
from flat_searcher.services.backup import backup_database
from flat_searcher.services.geocoding import GeocodingService
from flat_searcher.services.gemini_analysis import GeminiAnalysisProvider
from flat_searcher.services.infrastructure import InfrastructureRefreshService
from flat_searcher.services.location_scoring import LocationScoreService
from flat_searcher.services.processing import ListingProcessingService
from flat_searcher.services.scoring import ScoreRecalculationService
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
    _add_database_argument(init_db_parser)

    sync_parser = subparsers.add_parser("sync-listings", help="Fetch SS.com listings into SQLite.")
    _add_database_argument(sync_parser)
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
        "--ss-start-url",
        default=None,
        help="Override the SS.com list URL used as the first page for this sync run.",
    )
    sync_parser.add_argument(
        "--mark-missing-inactive",
        action="store_true",
        help="Mark active listings missing from this run as inactive. Use only for full syncs.",
    )

    ranking_parser = subparsers.add_parser(
        "show-ranking",
        help="Print ranked listings from SQLite.",
    )
    _add_database_argument(ranking_parser)
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

    detail_parser = subparsers.add_parser(
        "show-detail",
        help="Print one listing detail view model.",
    )
    detail_parser.add_argument(
        "listing_id",
        type=int,
        help="Internal listing database ID.",
    )
    _add_database_argument(detail_parser)
    detail_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )

    ui_parser = subparsers.add_parser("run-ui", help="Start the desktop UI.")
    _add_database_argument(ui_parser)
    ui_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    ui_parser.add_argument(
        "--language",
        choices=("en", "ru"),
        default="en",
        help="Initial desktop UI language.",
    )

    map_parser = subparsers.add_parser("show-map-markers", help="Print map marker JSON.")
    _add_database_argument(map_parser)
    map_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    map_parser.add_argument("--limit", type=int, default=None, help="Maximum markers to print.")

    geocode_parser = subparsers.add_parser(
        "geocode-listings",
        help="Geocode listings missing coordinates.",
    )
    _add_database_argument(geocode_parser)
    geocode_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum listings to geocode.",
    )

    analyze_parser = subparsers.add_parser(
        "analyze-listings",
        help="Run AI analysis storage pipeline.",
    )
    _add_database_argument(analyze_parser)
    analyze_parser.add_argument(
        "--listing-id",
        type=int,
        default=None,
        help="Analyze one listing ID.",
    )
    analyze_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum listings to analyze.",
    )
    analyze_parser.add_argument(
        "--version",
        default="mock-v1",
        help="Analysis version label stored with the result.",
    )
    analyze_parser.add_argument(
        "--force",
        action="store_true",
        help="Analyze selected listings even when a finished result already exists.",
    )
    _add_analysis_provider_arguments(analyze_parser)

    location_score_parser = subparsers.add_parser(
        "recalculate-location-scores",
        help="Recalculate RTU and central station distance scores.",
    )
    _add_database_argument(location_score_parser)

    infrastructure_parser = subparsers.add_parser(
        "refresh-infrastructure",
        help="Refresh cached OSM grocery and public transport POIs.",
    )
    _add_database_argument(infrastructure_parser)
    infrastructure_parser.add_argument(
        "--radius",
        type=int,
        default=1_800,
        help="Overpass search radius in meters. Minimum: 1800.",
    )
    infrastructure_parser.add_argument(
        "--max-age-hours",
        type=float,
        default=168,
        help="Reuse cache entries newer than this age.",
    )
    infrastructure_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum eligible listings to refresh.",
    )
    infrastructure_parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh even when a fresh cache entry exists.",
    )
    infrastructure_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile recalculated after the refresh.",
    )

    profiles_parser = subparsers.add_parser(
        "list-profiles",
        help="List available scoring profiles.",
    )
    _add_database_argument(profiles_parser)

    priors_parser = subparsers.add_parser(
        "seed-layout-priors",
        help="Seed the bundled typical-layout priors when the table is empty.",
    )
    _add_database_argument(priors_parser)

    backup_parser = subparsers.add_parser(
        "backup-db",
        help="Write a consistent backup copy of the SQLite database.",
    )
    _add_database_argument(backup_parser)
    backup_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Backup file or directory. Defaults to a timestamped file next to the database.",
    )

    score_parser = subparsers.add_parser(
        "recalculate-scores",
        help="Recalculate persisted apartment scores.",
    )
    _add_database_argument(score_parser)
    score_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    score_parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="Recalculate scores for every available profile.",
    )

    process_parser = subparsers.add_parser(
        "process-listings",
        help="Run pending analysis, location scoring and overall scoring.",
    )
    _add_database_argument(process_parser)
    process_parser.add_argument(
        "--listing-id",
        type=int,
        default=None,
        help="Analyze one listing ID before recalculation.",
    )
    process_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum pending listings to analyze.",
    )
    process_parser.add_argument(
        "--version",
        default="mock-v1",
        help="Analysis version label stored with the result.",
    )
    process_parser.add_argument(
        "--force-analysis",
        action="store_true",
        help="Analyze selected listings even when a finished result already exists.",
    )
    process_parser.add_argument(
        "--profile",
        default="for_living_mortgage",
        help="Scoring profile key.",
    )
    _add_analysis_provider_arguments(process_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding(sys.stdout)
    _configure_console_encoding(sys.stderr)
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    config = AppConfig.from_env(database_override=getattr(args, "database", None))
    configure_logging(log_file=config.log_file)

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
            start_url=args.ss_start_url or config.ss_start_url,
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
        _print_section("Flags", view_model.flags_lines)
        _print_section("Rating", view_model.rating_lines)
        _print_section("Price value", view_model.price_value_lines)
        _print_section("Layout", view_model.layout_lines)
        _print_section("Mortgage", view_model.mortgage_lines)
        _print_section("Location", view_model.location_lines)
        _print_section("History", view_model.history_lines)
        return 0

    if args.command == "run-ui":
        try:
            return run_desktop_app(
                DesktopUIConfig(
                    database_path=config.database_path,
                    profile_key=args.profile,
                    start_url=config.ss_start_url,
                    language=args.language,
                )
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
            geocoder=NominatimGeocoder(user_agent=config.geocoder_user_agent),
        ).geocode_missing(limit=args.limit)
        print(f"Checked listings: {result.checked_count}")
        print(f"Geocoded listings: {result.geocoded_count}")
        print(f"Location scores enabled: {result.score_enabled_count}")
        return 0

    if args.command == "analyze-listings":
        init_database(config.database_path)
        try:
            provider = _build_analysis_provider(args, config)
        except (GeminiSetupError, ValueError) as error:
            print(str(error))
            return 1
        result = AIAnalysisService(config.database_path, provider).analyze_pending(
            analysis_version=args.version,
            listing_id=args.listing_id,
            limit=args.limit,
            force=args.force,
        )
        print(f"Checked listings: {result.checked_count}")
        print(f"Analyzed listings: {result.analyzed_count}")
        print(f"Failed listings: {result.failed_count}")
        return 1 if result.failed_count else 0

    if args.command == "recalculate-location-scores":
        init_database(config.database_path)
        result = LocationScoreService(config.database_path).recalculate()
        print(f"Listings with geocoding: {result.listing_count}")
        print(f"Calculated location scores: {result.calculated_count}")
        print(f"Disabled location scores: {result.disabled_count}")
        return 0

    if args.command == "refresh-infrastructure":
        init_database(config.database_path)
        try:
            refresh_result = InfrastructureRefreshService(
                database_path=config.database_path,
                provider=OverpassPOIProvider(config.overpass_endpoint),
                source_endpoint=config.overpass_endpoint,
            ).refresh(
                radius_m=args.radius,
                max_age_hours=args.max_age_hours,
                limit=args.limit,
                force=args.force,
            )
            location_result = LocationScoreService(config.database_path).recalculate()
            scoring_result = ScoreRecalculationService(
                config.database_path
            ).recalculate(args.profile)
        except ValueError as error:
            print(str(error))
            return 1
        print(f"Eligible listings: {refresh_result.eligible_count}")
        print(f"Refreshed listings: {refresh_result.refreshed_count}")
        print(f"Fresh cache hits: {refresh_result.cached_count}")
        print(f"Failed refreshes: {refresh_result.failed_count}")
        print(f"Fetched POIs: {refresh_result.poi_count}")
        print(f"Location scores calculated: {location_result.calculated_count}")
        print(f"Overall scores calculated: {scoring_result.scored_count}")
        return 1 if refresh_result.failed_count else 0

    if args.command == "list-profiles":
        init_database(config.database_path)
        with open_database(config.database_path) as connection:
            repository = ProfileRepository(connection)
            repository.sync_builtin_profiles()
            summaries = repository.list_profiles()
        for summary in summaries:
            kind = "built-in" if summary.is_builtin else "custom"
            print(f"{summary.profile_key} - {summary.profile_name} ({kind})")
        return 0

    if args.command == "seed-layout-priors":
        init_database(config.database_path)
        with open_database(config.database_path) as connection:
            repository = LayoutPriorRepository(connection)
            seeded = repository.seed_default_priors()
            total = repository.count()
        print(f"Seeded priors: {seeded}")
        print(f"Total priors: {total}")
        return 0

    if args.command == "backup-db":
        try:
            result = backup_database(config.database_path, args.output)
        except FileNotFoundError as error:
            print(str(error))
            return 1
        print(f"Backed up: {result.source_path}")
        print(f"Backup file: {result.backup_path}")
        print(f"Backup size: {result.size_bytes} bytes")
        return 0

    if args.command == "recalculate-scores":
        init_database(config.database_path)
        service = ScoreRecalculationService(config.database_path)
        try:
            if args.all_profiles:
                with open_database(config.database_path) as connection:
                    profile_repository = ProfileRepository(connection)
                    profile_repository.sync_builtin_profiles()
                    profile_keys = [
                        summary.profile_key for summary in profile_repository.list_profiles()
                    ]
                for profile_key in profile_keys:
                    result = service.recalculate(profile_key)
                    print(f"{profile_key}: scored {result.scored_count}/{result.listing_count}")
                return 0
            result = service.recalculate(args.profile)
        except ValueError as error:
            print(str(error))
            return 1
        print(f"Active listings: {result.listing_count}")
        print(f"Scored listings: {result.scored_count}")
        return 0

    if args.command == "process-listings":
        init_database(config.database_path)
        try:
            provider = _build_analysis_provider(args, config)
            result = ListingProcessingService(
                database_path=config.database_path,
                analysis_provider=provider,
            ).process(
                analysis_version=args.version,
                profile_key=args.profile,
                listing_id=args.listing_id,
                limit=args.limit,
                force_analysis=args.force_analysis,
            )
        except (GeminiSetupError, ValueError) as error:
            print(str(error))
            return 1
        print(f"AI checked: {result.ai.checked_count}")
        print(f"AI analyzed: {result.ai.analyzed_count}")
        print(f"AI failed: {result.ai.failed_count}")
        print(f"Location scores calculated: {result.location.calculated_count}")
        print(f"Location scores disabled: {result.location.disabled_count}")
        print(f"Overall scores calculated: {result.scoring.scored_count}")
        return 1 if result.ai.failed_count else 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _print_config(config: AppConfig) -> None:
    print(f"App home: {config.app_home}")
    print(f"Database: {config.database_path}")
    print(f"Cache dir: {config.cache_dir}")
    print(f"Temporary images dir: {config.temporary_images_dir}")
    print(f"Floor plans dir: {config.floor_plans_dir}")
    print(f"Log file: {config.log_file}")
    print(f"SS start URL: {config.ss_start_url}")
    print(f"Gemini model: {config.gemini_model}")
    print(f"Overpass endpoint: {config.overpass_endpoint}")
    print(f"Geocoder user agent: {config.geocoder_user_agent}")


def _configure_console_encoding(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to FLAT_SEARCHER_DB_PATH or app home.",
    )


def _add_analysis_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic mock analysis for local pipeline testing.",
    )
    parser.add_argument(
        "--gemini",
        action="store_true",
        help="Use Gemini with GEMINI_API_KEY from the environment.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Gemini model override. Defaults to GEMINI_MODEL or the app default.",
    )
    parser.add_argument(
        "--image-request-delay",
        type=float,
        default=0.1,
        help="Delay between listing image downloads in seconds.",
    )
    parser.add_argument(
        "--pass1-json",
        type=Path,
        default=None,
        help="Pass 1 JSON file.",
    )
    parser.add_argument(
        "--pass2-json",
        type=Path,
        default=None,
        help="Pass 2 JSON file.",
    )


def _build_analysis_provider(args, config: AppConfig) -> AIAnalysisProvider:
    selected_provider_count = sum(
        (
            bool(args.mock),
            bool(args.gemini),
            args.pass1_json is not None or args.pass2_json is not None,
        )
    )
    if selected_provider_count != 1:
        raise ValueError(
            "Choose exactly one provider: --mock, --gemini, or both JSON files."
        )
    if args.mock:
        return MockAIAnalysisProvider()
    if args.gemini:
        config.ensure_runtime_directories()
        model_client = GeminiModelClient(
            api_key=config.gemini_api_key or "",
            model=args.model or config.gemini_model,
        )
        return GeminiAnalysisProvider(
            pipeline=AIAnalysisPipeline(model_client),
            image_downloader=ImageDownloader(
                temporary_images_dir=config.temporary_images_dir,
                floor_plans_dir=config.floor_plans_dir,
                fetcher=HttpTextClient(
                    request_delay_seconds=max(0.0, args.image_request_delay)
                ),
            ),
        )
    if args.pass1_json is None or args.pass2_json is None:
        raise ValueError("Both --pass1-json and --pass2-json are required.")
    return JsonAIAnalysisProvider(args.pass1_json, args.pass2_json)


def _print_section(title: str, lines: tuple[str, ...]) -> None:
    print("")
    print(f"{title}:")
    for line in lines:
        print(f"  {line}")
