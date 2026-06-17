"""Runtime configuration for Flat Searcher."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SS_START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"


@dataclass(frozen=True)
class AppConfig:
    app_home: Path
    database_path: Path
    cache_dir: Path
    temporary_images_dir: Path
    floor_plans_dir: Path
    ss_start_url: str = DEFAULT_SS_START_URL

    @classmethod
    def from_env(cls, database_override: Path | None = None) -> "AppConfig":
        app_home = Path(
            os.environ.get("FLAT_SEARCHER_HOME") or Path.home() / ".flat_searcher"
        ).expanduser()

        database_from_env = os.environ.get("FLAT_SEARCHER_DB_PATH")
        database_path = database_override or (
            Path(database_from_env).expanduser()
            if database_from_env
            else app_home / "flat_searcher.sqlite3"
        )

        cache_dir = app_home / "cache"
        temporary_images_dir = cache_dir / "tmp_images"
        floor_plans_dir = cache_dir / "floor_plans"

        return cls(
            app_home=app_home,
            database_path=database_path,
            cache_dir=cache_dir,
            temporary_images_dir=temporary_images_dir,
            floor_plans_dir=floor_plans_dir,
        )

    def ensure_runtime_directories(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.temporary_images_dir.mkdir(parents=True, exist_ok=True)
        self.floor_plans_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
