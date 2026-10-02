"""Small listing photos, cached on disk and published beside the digest page."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from flat_searcher.scraper.http_client import BinaryFetchResult, FetchError

GALLERY_SUFFIX = ".800.jpg"
THUMBNAIL_SUFFIX = ".t.jpg"


def thumbnail_url(record: Mapping[str, Any]) -> str | None:
    images = record.get("images") or []
    if not images:
        return None
    url = str(images[0].get("url") or "")
    if not url:
        return None
    return url[: -len(GALLERY_SUFFIX)] + THUMBNAIL_SUFFIX if url.endswith(GALLERY_SUFFIX) else url


class ThumbnailCache:
    def __init__(self, directory: Path, fetch_bytes: Callable[[str], BinaryFetchResult]) -> None:
        self._directory = directory
        self._fetch = fetch_bytes

    def content(self, url: str) -> bytes | None:
        path = self._directory / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")
        if not path.exists():
            try:
                content = self._fetch(url).content
            except (FetchError, OSError):
                return None
            self._directory.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temp.write_bytes(content)
            os.replace(temp, path)
        return path.read_bytes()
