"""HTTP client for polite SS.com fetching."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = "FlatSearcher/0.1 (+local desktop apartment analysis)"


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str


@dataclass(frozen=True)
class BinaryFetchResult:
    url: str
    content: bytes
    content_type: str | None = None


class FetchError(RuntimeError):
    pass


class HttpTextClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 30.0,
        request_delay_seconds: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.request_delay_seconds = request_delay_seconds
        self._last_request_at = 0.0

    def fetch_text(self, url: str) -> FetchResult:
        self._wait_if_needed()
        request = Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return FetchResult(url=response.geturl(), text=body.decode(charset, errors="replace"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise FetchError(f"Failed to fetch {url}: {error}") from error
        finally:
            self._last_request_at = time.monotonic()

    def fetch_bytes(self, url: str) -> BinaryFetchResult:
        self._wait_if_needed()
        request = Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return BinaryFetchResult(
                    url=response.geturl(),
                    content=response.read(),
                    content_type=response.headers.get_content_type(),
                )
        except (HTTPError, URLError, TimeoutError) as error:
            raise FetchError(f"Failed to fetch {url}: {error}") from error
        finally:
            self._last_request_at = time.monotonic()

    def _wait_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_seconds = self.request_delay_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
