"""HTTP client for polite SS.com fetching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 FlatSearcher/0.1"
)


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str


@dataclass(frozen=True)
class BinaryFetchResult:
    url: str
    content: bytes


class FetchError(RuntimeError):
    pass


class HttpTextClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 20.0,
        request_delay_seconds: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.request_delay_seconds = request_delay_seconds
        self._last_request_at = 0.0

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "lv,en-US;q=0.8,en;q=0.7,ru;q=0.6",
            "Connection": "close",
        }

    def fetch_text(self, url: str) -> FetchResult:
        self._wait_if_needed()
        request = Request(url, headers=self._request_headers())
        logger.debug("HTTP GET text start: %s", url)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                final_url = response.geturl()
                logger.debug(
                    "HTTP GET text done: %s -> %s, status=%s, bytes=%s",
                    url,
                    final_url,
                    getattr(response, "status", "unknown"),
                    len(body),
                )
                return FetchResult(url=final_url, text=body.decode(charset, errors="replace"))
        except (HTTPError, URLError, TimeoutError) as error:
            logger.exception("HTTP GET text failed: %s", url)
            raise FetchError(f"Failed to fetch {url}: {error}") from error
        finally:
            self._last_request_at = time.monotonic()

    def fetch_bytes(self, url: str) -> BinaryFetchResult:
        self._wait_if_needed()
        request = Request(url, headers=self._request_headers())
        logger.debug("HTTP GET bytes start: %s", url)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read()
                final_url = response.geturl()
                logger.debug(
                    "HTTP GET bytes done: %s -> %s, status=%s, bytes=%s",
                    url,
                    final_url,
                    getattr(response, "status", "unknown"),
                    len(content),
                )
                return BinaryFetchResult(
                    url=final_url,
                    content=content,
                )
        except (HTTPError, URLError, TimeoutError) as error:
            logger.exception("HTTP GET bytes failed: %s", url)
            raise FetchError(f"Failed to fetch {url}: {error}") from error
        finally:
            self._last_request_at = time.monotonic()

    def _wait_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_seconds = self.request_delay_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
