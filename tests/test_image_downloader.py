import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

from flat_searcher.images import ImageDownloader


@dataclass(frozen=True)
class FakeBinaryResult:
    url: str
    content: bytes


class FakeBinaryFetcher:
    def fetch_bytes(self, url: str) -> FakeBinaryResult:
        return FakeBinaryResult(url=url, content=f"image:{url}".encode())


class ImageDownloaderTests(TestCase):
    def test_downloads_temporary_images_caches_floor_plan_and_cleans_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloader = ImageDownloader(
                temporary_images_dir=root / "tmp_images",
                floor_plans_dir=root / "floor_plans",
                fetcher=FakeBinaryFetcher(),
            )

            downloaded = downloader.download_listing_images(
                listing_id=10,
                image_urls=("https://i.ss.com/gallery/a.jpg", "https://i.ss.com/gallery/b.jpg"),
                run_id="run-1",
            )

            self.assertEqual(len(downloaded), 2)
            self.assertTrue(downloaded[0].temporary_path.exists())
            self.assertTrue(downloaded[1].temporary_path.exists())

            cached_floor_plan = downloader.cache_floor_plan(10, downloaded[0])
            self.assertTrue(cached_floor_plan.exists())

            downloader.cleanup_run("run-1")

            self.assertFalse((root / "tmp_images" / "run-1").exists())
            self.assertTrue(cached_floor_plan.exists())
