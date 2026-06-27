"""Runtime configuration for Flat Searcher."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SS_START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"


@dataclass(frozen=True)
class AppConfig:
    app_home: Path
    database_path: Path
    cache_dir: Path
    temporary_images_dir: Path
    floor_plans_dir: Path
    ss_start_url: str = DEFAULT_SS_START_URL
    gemini_api_key: str | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    overpass_endpoint: str = DEFAULT_OVERPASS_ENDPOINT

    @classmethod
    def from_env(cls, database_override: Path | None = None) -> "AppConfig":
        _load_environment_file(
            Path(os.environ.get("FLAT_SEARCHER_ENV_FILE") or ".env").expanduser()
        )
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
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            gemini_model=os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
            overpass_endpoint=(
                os.environ.get("OVERPASS_ENDPOINT") or DEFAULT_OVERPASS_ENDPOINT
            ),
        )

    def ensure_runtime_directories(self) -> None:
        self.app_home.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.temporary_images_dir.mkdir(parents=True, exist_ok=True)
        self.floor_plans_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


def _load_environment_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
