"""Runtime configuration for Flat Searcher."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SS_START_URL = "https://www.ss.com/lv/real-estate/flats/riga/all/sell/"


@dataclass(frozen=True)
class AppConfig:
    app_home: Path
    cache_dir: Path
    log_file: Path
    ss_start_url: str = DEFAULT_SS_START_URL
    pages_repo: str | None = None
    pages_token: str | None = None

    @classmethod
    def from_env(cls) -> "AppConfig":
        _load_environment_file(
            Path(os.environ.get("FLAT_SEARCHER_ENV_FILE") or ".env").expanduser()
        )
        app_home = Path(
            os.environ.get("FLAT_SEARCHER_HOME") or _user_home() / ".flat_searcher"
        ).expanduser()
        return cls(
            app_home=app_home,
            cache_dir=app_home / "cache",
            log_file=app_home / "logs" / "flat_searcher.log",
            ss_start_url=os.environ.get("FLAT_SEARCHER_SS_START_URL") or DEFAULT_SS_START_URL,
            pages_repo=os.environ.get("FLAT_SEARCHER_PAGES_REPO") or None,
            pages_token=os.environ.get("FLAT_SEARCHER_PAGES_TOKEN") or None,
        )


def _user_home() -> Path:
    try:
        return Path.home()
    except RuntimeError:
        fallback = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.environ.get("TEMP")
            or os.getcwd()
        )
        return Path(fallback)


def _load_environment_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
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
