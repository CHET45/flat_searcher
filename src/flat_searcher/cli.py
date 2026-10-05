"""Command line entry points: index, judge, transit, digest, publish and show-config."""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Sequence, TextIO

from flat_searcher.config import AppConfig
from flat_searcher.indexing import IndexerOptions, IndexerRun
from flat_searcher.judging import JudgeFilters, JudgeOptions, JudgeRun, OllamaJudgeClient
from flat_searcher.judging.ollama import DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL
from flat_searcher.library import (
    GitLibraryStore,
    LibraryError,
    LibraryStore,
    LocalLibraryStore,
)
from flat_searcher.logging_config import configure_logging
from flat_searcher.scraper.http_client import HttpTextClient
from flat_searcher.shortlist.criteria import Criteria, CriteriaError, changed_targets, parse_criteria
from flat_searcher.shortlist.digest import render_markdown, select
from flat_searcher.shortlist.page import MAP_SCRIPT, PHOTO_DIR, render_page
from flat_searcher.publishing import PublishError, publish_site
from flat_searcher.shortlist.thumbnails import ThumbnailCache, thumbnail_url
from flat_searcher.transit.run import TransitRun
from flat_searcher.transit.sources import TransitSourceError, TransitSources

LIBRARY_PATH_VARIABLE = "FLAT_SEARCHER_LIBRARY_PATH"
LIBRARY_REPO_VARIABLE = "FLAT_SEARCHER_LIBRARY_REPO"
LIBRARY_TOKEN_VARIABLE = "FLAT_SEARCHER_LIBRARY_TOKEN"
LIBRARY_BRANCH_VARIABLE = "FLAT_SEARCHER_LIBRARY_BRANCH"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flat-searcher",
        description="Riga apartment listings indexer for SS.com.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show-config", help="Print resolved runtime paths and defaults.")

    index_parser = subparsers.add_parser(
        "index",
        help="Crawl SS.com into the JSONL library. Library location comes from the environment.",
    )
    index_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop discovering listings after this many (development runs).",
    )
    index_parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Delay between HTTP requests in seconds.",
    )
    index_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Crawl and diff, but write nothing to the library.",
    )
    _add_env_file_argument(index_parser)

    judge_parser = subparsers.add_parser(
        "judge",
        help="Judge a day's queue with a local Ollama model and append verdicts to the library.",
    )
    judge_parser.add_argument(
        "--day",
        default=None,
        help="Queue day (YYYY-MM-DD). Default: the latest queue in the library.",
    )
    judge_parser.add_argument(
        "--model",
        default=OLLAMA_DEFAULT_MODEL,
        help=f"Ollama model name. Default: {OLLAMA_DEFAULT_MODEL}.",
    )
    judge_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Judge at most this many pending entries.",
    )
    judge_parser.add_argument(
        "--max-images",
        type=int,
        default=8,
        help="Images attached per listing, in gallery order.",
    )
    judge_parser.add_argument("--max-price", type=int, default=None, help="Skip pricier listings.")
    judge_parser.add_argument(
        "--min-rooms", type=int, default=None, help="Skip listings declaring fewer rooms."
    )
    judge_parser.add_argument(
        "--districts",
        default=None,
        help="Comma-separated district names to keep (case-insensitive).",
    )
    judge_parser.add_argument(
        "--instructions",
        default="docs/ai-instructions.md",
        help="Judgment instructions handed to the model as the system prompt.",
    )
    judge_parser.add_argument(
        "--ids",
        default=None,
        help="Comma-separated ss_ids to judge; everything else in the queue is skipped.",
    )
    judge_parser.add_argument(
        "--force",
        action="store_true",
        help="Judge again even when a verdict already exists (the newer verdict wins on merge).",
    )
    judge_parser.add_argument(
        "--shard",
        default="0/1",
        help="Take every Nth pending entry, as INDEX/COUNT, so several judges can share a queue.",
    )
    judge_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Judge but write no verdicts.",
    )
    _add_env_file_argument(judge_parser)

    transit_parser = subparsers.add_parser(
        "transit",
        help="Locate active listings and measure public transport journeys to the criteria targets.",
    )
    transit_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download the timetable and the address register even if the cache is fresh.",
    )
    _add_env_file_argument(transit_parser)

    digest_parser = subparsers.add_parser(
        "digest",
        help="Write the ordered shortlist for a day to the library (needs criteria.toml).",
    )
    digest_parser.add_argument(
        "--day", default=None, help="Digest day (YYYY-MM-DD). Default: today."
    )
    _add_env_file_argument(digest_parser)

    publish_parser = subparsers.add_parser(
        "publish",
        help="Push a day's digest page to the GitHub Pages repository that shares it.",
    )
    publish_parser.add_argument(
        "--day", default=None, help="Digest day (YYYY-MM-DD). Default: today."
    )
    _add_env_file_argument(publish_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding(sys.stdout)
    _configure_console_encoding(sys.stderr)
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    env_file = getattr(args, "env_file", None)
    if env_file:
        os.environ["FLAT_SEARCHER_ENV_FILE"] = env_file
    config = AppConfig.from_env()
    configure_logging(log_file=config.log_file)

    if args.command == "show-config":
        _print_config(config)
        return 0
    if args.command == "index":
        return _run_index(config, args)
    if args.command == "judge":
        return _run_judge(config, args)
    if args.command == "transit":
        return _run_transit(config, args)
    if args.command == "digest":
        return _run_digest(config, args)
    if args.command == "publish":
        return _run_publish(config, args)

    parser.error(f"Unknown command: {args.command}")
    return 2


def _open_library(config: AppConfig) -> LibraryStore | None:
    library_repo = os.environ.get(LIBRARY_REPO_VARIABLE)
    library_path = os.environ.get(LIBRARY_PATH_VARIABLE)
    if library_repo:
        return GitLibraryStore(
            repo_url=library_repo,
            token=os.environ.get(LIBRARY_TOKEN_VARIABLE, ""),
            workdir=config.cache_dir / "library-repo",
            branch=os.environ.get(LIBRARY_BRANCH_VARIABLE) or "main",
        )
    if library_path:
        return LocalLibraryStore(Path(library_path).expanduser())
    print(f"Set {LIBRARY_PATH_VARIABLE} or {LIBRARY_REPO_VARIABLE} to choose the library.")
    return None


def _run_index(config: AppConfig, args: argparse.Namespace) -> int:
    store = _open_library(config)
    if store is None:
        return 2
    git_store = store if isinstance(store, GitLibraryStore) else None

    options = IndexerOptions(
        start_url=config.ss_start_url,
        limit=args.limit,
        request_delay_seconds=args.request_delay,
        dry_run=args.dry_run,
    )
    try:
        if git_store is not None:
            git_store.pull()
        result = IndexerRun(store, options).run()
        pushed = False
        if git_store is not None and not args.dry_run:
            pushed = git_store.push(f"Index {result.meta['run_date']}")
    except LibraryError as error:
        # Library errors are already redacted by the store; nothing else may print them.
        print(f"Library error: {error}")
        return 1

    for stage in result.stages:
        detail = ", ".join(f"{key}={value}" for key, value in stage.counts.items())
        line = f"{stage.name}: {stage.status}"
        if stage.error:
            line += f" ({stage.error})"
        elif detail:
            line += f" ({detail})"
        print(line)
    if result.meta.get("deactivation_skipped"):
        print("Removal skipped: incomplete crawl.")
    if git_store is not None and not args.dry_run:
        print("Library pushed." if pushed else "Library unchanged, nothing pushed.")
    return 0 if result.ok else 1


def _run_judge(config: AppConfig, args: argparse.Namespace) -> int:
    store = _open_library(config)
    if store is None:
        return 2
    if isinstance(store, GitLibraryStore):
        store.pull()
    day = args.day or (store.queue_days() or [None])[-1]
    if day is None:
        print("No queue in the library yet; run `index` first.")
        return 1
    client = OllamaJudgeClient(model=args.model)
    if not client.available():
        print(f"Ollama is not reachable at {client.base_url}; start it with `ollama serve`.")
        return 1
    districts = frozenset(
        part.strip().casefold() for part in (args.districts or "").split(",") if part.strip()
    )
    options = JudgeOptions(
        day=day,
        instructions_path=Path(args.instructions),
        model_name=client.name,
        limit=args.limit,
        max_images=max(0, args.max_images),
        filters=JudgeFilters(
            max_price=args.max_price, min_rooms=args.min_rooms, districts=districts
        ),
        only_ids=frozenset(part.strip() for part in (args.ids or "").split(",") if part.strip()),
        force=args.force,
        shard=_parse_shard(args.shard),
        dry_run=args.dry_run,
    )

    def report(done: int, total: int, outcome: str) -> None:
        print(f"[{done}/{total}] {outcome}", flush=True)

    result = JudgeRun(
        store,
        options,
        client.ask,
        HttpTextClient(request_delay_seconds=0.3),
    ).run(progress=report)
    print(
        f"day {result.day}: queued={result.queued} eligible={result.eligible} "
        f"already_judged={result.already_judged} judged={result.judged} failed={result.failed}"
    )
    if result.abort_reason:
        print(result.abort_reason)
    if isinstance(store, GitLibraryStore) and not args.dry_run and result.judged:
        store.push(f"Judge {day}")
        print("Library pushed.")
    return 0 if result.ok else 1


def _load_criteria(store: LibraryStore) -> tuple[Criteria | None, bool]:
    text = store.read_criteria()
    if text is None:
        return None, True
    try:
        return parse_criteria(text), True
    except CriteriaError as error:
        print(f"Criteria error: {error}")
        return None, False


def _run_transit(config: AppConfig, args: argparse.Namespace) -> int:
    store = _open_library(config)
    if store is None:
        return 2
    try:
        if isinstance(store, GitLibraryStore):
            store.pull()
        criteria, valid = _load_criteria(store)
        if not valid:
            return 1
        if criteria is None:
            print("No criteria.toml in the library: listings are located, no target is checked.")
        sources = TransitSources(config.cache_dir / "transit", refresh=args.refresh)
        result = TransitRun(store, sources, criteria, datetime.now().astimezone()).run()
        if isinstance(store, GitLibraryStore):
            store.push("Transit")
    except LibraryError as error:
        print(f"Library error: {error}")
        return 1
    except (OSError, TransitSourceError, zipfile.BadZipFile, ValueError, KeyError) as error:
        print(f"Transit sources failed: {type(error).__name__}")
        return 1
    print(
        f"transit: exact={result.exact} approx={result.approx} unlocated={result.unlocated} "
        f"targets={result.targets} unresolved_targets={result.unresolved_targets} "
        f"reached_any={result.reached_any} buildings={result.buildings}"
    )
    return 0


def _run_digest(config: AppConfig, args: argparse.Namespace) -> int:
    store = _open_library(config)
    if store is None:
        return 2
    try:
        if isinstance(store, GitLibraryStore):
            store.pull()
        criteria, valid = _load_criteria(store)
        if not valid:
            return 1
        if criteria is None:
            print("No criteria.toml in the library: nothing to rank, no digest written.")
            return 0
        targets = store.read_transit_targets()
        changed = changed_targets(criteria, targets)
        if changed:
            print(
                f"targets_changed={len(changed)}: the last transit run did not compute them as "
                "criteria.toml defines them now; run transit, then digest."
            )
            return 1
        day = args.day or datetime.now().astimezone().date().isoformat()
        previous = [earlier for earlier in store.digest_days() if earlier < day]
        selection = select(
            store.load_listings(),
            criteria,
            store.read_transit(),
            store.read_events(),
            previous[-1] if previous else None,
        )
        counts = selection.counts
        store.write_digest(day, render_markdown(selection, criteria, day))
        cache = ThumbnailCache(
            config.cache_dir / "thumbnails", HttpTextClient(request_delay_seconds=0.3).fetch_bytes
        )
        photos: dict[str, bytes] = {}
        for item in selection.candidates:
            url = thumbnail_url(item.record)
            photo = cache.content(url) if url else None
            if photo:
                photos[item.ss_id] = photo
        page = render_page(selection, criteria, day, photos.keys(), targets, store.read_transit_map())
        store.write_digest_site(
            day,
            {
                "index.html": page.html,
                MAP_SCRIPT: page.map_script,
                **{f"{PHOTO_DIR}/{ss_id}.jpg": photos[ss_id] for ss_id in page.photo_ids},
            },
        )
        if isinstance(store, GitLibraryStore):
            store.push(f"Digest {day}")
    except LibraryError as error:
        print(f"Library error: {error}")
        return 1
    rejected = " ".join(f"rejected_{gate}={count}" for gate, count in counts.rejected.items())
    print(
        f"digest {day}: active={counts.active} candidates={counts.candidates} "
        f"flats={page.flats} new={counts.new} price_drops={counts.price_drops} "
        f"removed={counts.removed} photos={len(page.photo_ids)} "
        f"{rejected}".rstrip()
    )
    return 0


def _run_publish(config: AppConfig, args: argparse.Namespace) -> int:
    if not config.pages_repo:
        print("No FLAT_SEARCHER_PAGES_REPO: nothing published.")
        return 0
    store = _open_library(config)
    if store is None:
        return 2
    day = args.day or datetime.now().astimezone().date().isoformat()
    try:
        if isinstance(store, GitLibraryStore):
            store.pull()
        site = store.digest_site(day)
    except LibraryError as error:
        print(f"Library error: {error}")
        return 1
    if site is None:
        print(f"No digest page for {day}: run digest first.")
        return 1
    try:
        files, size = publish_site(site, day, config.pages_repo, config.pages_token)
    except (OSError, PublishError) as error:
        print(f"Publish failed: {type(error).__name__}")
        return 1
    print(f"published day={day} files={files} bytes={size}")
    return 0


def _parse_shard(text: str) -> tuple[int, int]:
    index_text, _, count_text = text.partition("/")
    index, count = int(index_text), int(count_text or 1)
    if count < 1 or not 0 <= index < count:
        raise SystemExit(f"--shard must be INDEX/COUNT with 0 <= INDEX < COUNT, got {text!r}")
    return index, count


def _print_config(config: AppConfig) -> None:
    print(f"App home: {config.app_home}")
    print(f"Cache dir: {config.cache_dir}")
    print(f"Log file: {config.log_file}")
    print(f"SS start URL: {config.ss_start_url}")
    print(f"Library path variable: {LIBRARY_PATH_VARIABLE}")
    print(f"Library repo variable: {LIBRARY_REPO_VARIABLE}")


def _add_env_file_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        default=None,
        help="Load environment variables from this file before resolving configuration.",
    )


def _configure_console_encoding(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
