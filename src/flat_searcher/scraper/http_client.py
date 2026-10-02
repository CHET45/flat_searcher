"""HTTP client for polite SS.com fetching."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from http.client import HTTPException
from threading import Lock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 FlatSearcher/0.1"
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0


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
        max_retries: int = 2,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = retry_backoff_seconds
        self._throttle_lock = Lock()
        self._next_request_at = 0.0

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "lv,en-US;q=0.8,en;q=0.7,ru;q=0.6",
            "Connection": "close",
        }

    def fetch_text(self, url: str) -> FetchResult:
        body, final_url, charset = self._fetch(url)
        return FetchResult(url=final_url, text=body.decode(charset, errors="replace"))

    def fetch_bytes(self, url: str) -> BinaryFetchResult:
        body, final_url, _ = self._fetch(url)
        return BinaryFetchResult(url=final_url, content=body)

    def _fetch(self, url: str) -> tuple[bytes, str, str]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_if_needed()
            request = Request(url, headers=self._request_headers())
            logger.debug("HTTP GET start: %s (attempt %s)", url, attempt + 1)
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read()
                    charset = response.headers.get_content_charset() or "utf-8"
                    final_url = response.geturl()
                    logger.debug(
                        "HTTP GET done: %s -> %s, status=%s, bytes=%s",
                        url,
                        final_url,
                        getattr(response, "status", "unknown"),
                        len(body),
                    )
                    return body, final_url, charset
            except (OSError, HTTPException) as error:
                last_error = error
                if attempt >= self.max_retries or not _is_retryable(error):
                    logger.exception("HTTP GET failed: %s", url)
                    raise FetchError(f"Failed to fetch {url}: {error}") from error
                delay = self._retry_delay(error, attempt)
                logger.warning(
                    "HTTP GET retrying in %.1fs: %s (attempt %s/%s): %s",
                    delay,
                    url,
                    attempt + 1,
                    self.max_retries + 1,
                    error,
                )
                time.sleep(delay)
        raise FetchError(f"Failed to fetch {url}: {last_error}") from last_error

    def _retry_delay(self, error: Exception, attempt: int) -> float:
        if isinstance(error, HTTPError):
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after:
                try:
                    return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
                except ValueError:
                    pass
        return self.retry_backoff_seconds * (2**attempt)

    def _wait_if_needed(self) -> None:
        # Reserve the next request slot under the lock so concurrent fetch
        # workers sharing this client keep the polite per-request spacing.
        with self._throttle_lock:
            now = time.monotonic()
            start_at = max(now, self._next_request_at)
            self._next_request_at = start_at + self.request_delay_seconds
            wait_seconds = start_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code in RETRYABLE_STATUS_CODES
    return isinstance(error, (OSError, HTTPException))
