"""Temporary image downloading and floor plan caching."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class BinaryFetcher(Protocol):
    def fetch_bytes(self, url: str): ...


@dataclass(frozen=True)
class DownloadedImage:
    source_url: str
    temporary_path: Path
    content_hash: str


class ImageDownloader:
    def __init__(
        self,
        temporary_images_dir: Path,
        floor_plans_dir: Path,
        fetcher: BinaryFetcher,
    ) -> None:
        self.temporary_images_dir = temporary_images_dir
        self.floor_plans_dir = floor_plans_dir
        self.fetcher = fetcher

    def download_listing_images(
        self,
        listing_id: int,
        image_urls: tuple[str, ...],
        run_id: str,
    ) -> tuple[DownloadedImage, ...]:
        listing_dir = self._listing_temp_dir(listing_id, run_id)
        listing_dir.mkdir(parents=True, exist_ok=True)

        downloaded_images = []
        for index, image_url in enumerate(image_urls, start=1):
            fetch_result = self.fetcher.fetch_bytes(image_url)
            content_hash = hashlib.sha256(fetch_result.content).hexdigest()
            path = listing_dir / _image_file_name(index, image_url, content_hash)
            path.write_bytes(fetch_result.content)
            downloaded_images.append(
                DownloadedImage(
                    source_url=image_url,
                    temporary_path=path,
                    content_hash=content_hash,
                )
            )
        return tuple(downloaded_images)

    def cache_floor_plan(self, listing_id: int, image: DownloadedImage) -> Path:
        self.floor_plans_dir.mkdir(parents=True, exist_ok=True)
        target = self.floor_plans_dir / f"{listing_id}_{image.content_hash[:16]}{image.temporary_path.suffix}"
        shutil.copyfile(image.temporary_path, target)
        return target

    def cleanup_run(self, run_id: str) -> None:
        run_dir = self.temporary_images_dir / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)

    def _listing_temp_dir(self, listing_id: int, run_id: str) -> Path:
        return self.temporary_images_dir / run_id / str(listing_id)


def _image_file_name(index: int, image_url: str, content_hash: str) -> str:
    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".img"
    return f"{index:03d}_{content_hash[:16]}{suffix}"
