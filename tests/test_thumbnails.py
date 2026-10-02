import tempfile
from pathlib import Path
from unittest import TestCase

from flat_searcher.scraper.http_client import BinaryFetchResult, FetchError
from flat_searcher.shortlist.thumbnails import ThumbnailCache, thumbnail_url

GALLERY = "https://i.ss.com/gallery/3/514/128378/flats-riga-agenskalns-25675536.800.jpg"
JPEG = bytes([0xFF, 0xD8]) + b"jpeg"
THUMB = "https://i.ss.com/gallery/3/514/128378/flats-riga-agenskalns-25675536.t.jpg"


class FakeFetcher:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.urls: list[str] = []

    def __call__(self, url: str) -> BinaryFetchResult:
        self.urls.append(url)
        if self.failing:
            raise FetchError(f"no {url}")
        return BinaryFetchResult(url=url, content=JPEG)


class ThumbnailUrlTests(TestCase):
    def test_first_gallery_image_in_its_small_size(self) -> None:
        record = {"images": [{"url": GALLERY}, {"url": "https://i.ss.com/gallery/x.800.jpg"}]}
        self.assertEqual(thumbnail_url(record), THUMB)

    def test_no_images_no_thumbnail(self) -> None:
        self.assertIsNone(thumbnail_url({"images": []}))
        self.assertIsNone(thumbnail_url({}))


class ThumbnailCacheTests(TestCase):
    def test_fetched_once_then_served_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fetcher = FakeFetcher()
            cache = ThumbnailCache(Path(temp_dir), fetcher)
            self.assertEqual(cache.content(THUMB), JPEG)
            self.assertEqual(ThumbnailCache(Path(temp_dir), fetcher).content(THUMB), JPEG)
            self.assertEqual(fetcher.urls, [THUMB])

    def test_a_failed_fetch_gives_no_photo_and_caches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = ThumbnailCache(Path(temp_dir), FakeFetcher(failing=True))
            self.assertIsNone(cache.content(THUMB))
            self.assertEqual(list(Path(temp_dir).iterdir()), [])
