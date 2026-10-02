from email.message import Message
from http.client import IncompleteRead
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from flat_searcher.scraper.http_client import FetchError, HttpTextClient


class FakeResponse:
    def __init__(self, body: bytes, url: str = "https://example.com/") -> None:
        self._body = body
        self._url = url
        self.headers = Message()
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> None:
        pass


class FailingReadResponse(FakeResponse):
    def __init__(self, error: Exception) -> None:
        super().__init__(b"")
        self._error = error

    def read(self) -> bytes:
        raise self._error


def _http_error(code: int, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.com/", code, "error", headers, BytesIO(b""))


def _client() -> HttpTextClient:
    return HttpTextClient(request_delay_seconds=0.0, max_retries=2, retry_backoff_seconds=0.5)


def test_fetch_text_returns_decoded_body():
    with patch(
        "flat_searcher.scraper.http_client.urlopen",
        return_value=FakeResponse(b"hello"),
    ):
        result = _client().fetch_text("https://example.com/")
    assert result.text == "hello"
    assert result.url == "https://example.com/"


def test_retries_on_server_error_then_succeeds():
    attempts = [_http_error(503), _http_error(503), FakeResponse(b"ok")]
    with (
        patch("flat_searcher.scraper.http_client.urlopen", side_effect=attempts),
        patch("flat_searcher.scraper.http_client.time.sleep") as sleep,
    ):
        result = _client().fetch_text("https://example.com/")
    assert result.text == "ok"
    delays = [call.args[0] for call in sleep.call_args_list]
    assert 0.5 in delays and 1.0 in delays


def test_respects_retry_after_header():
    attempts = [_http_error(429, retry_after="3"), FakeResponse(b"ok")]
    with (
        patch("flat_searcher.scraper.http_client.urlopen", side_effect=attempts),
        patch("flat_searcher.scraper.http_client.time.sleep") as sleep,
    ):
        _client().fetch_text("https://example.com/")
    assert 3.0 in [call.args[0] for call in sleep.call_args_list]


def test_does_not_retry_client_errors():
    with (
        patch(
            "flat_searcher.scraper.http_client.urlopen", side_effect=_http_error(404)
        ) as urlopen,
        pytest.raises(FetchError),
    ):
        _client().fetch_text("https://example.com/")
    assert urlopen.call_count == 1


def test_raises_after_exhausting_retries():
    with (
        patch(
            "flat_searcher.scraper.http_client.urlopen",
            side_effect=URLError("connection reset"),
        ) as urlopen,
        patch("flat_searcher.scraper.http_client.time.sleep"),
        pytest.raises(FetchError),
    ):
        _client().fetch_text("https://example.com/")
    assert urlopen.call_count == 3


def test_fetch_bytes_retries_on_timeout():
    attempts = [TimeoutError("timed out"), FakeResponse(b"\x89PNG")]
    with (
        patch("flat_searcher.scraper.http_client.urlopen", side_effect=attempts),
        patch("flat_searcher.scraper.http_client.time.sleep"),
    ):
        result = _client().fetch_bytes("https://example.com/img.png")
    assert result.content == b"\x89PNG"


def test_retries_connection_reset_mid_read_then_succeeds():
    attempts = [
        FailingReadResponse(ConnectionResetError(10054, "connection reset")),
        FakeResponse(b"ok"),
    ]
    with (
        patch("flat_searcher.scraper.http_client.urlopen", side_effect=attempts),
        patch("flat_searcher.scraper.http_client.time.sleep"),
    ):
        result = _client().fetch_text("https://example.com/")
    assert result.text == "ok"


def test_retries_incomplete_read_then_succeeds():
    attempts = [FailingReadResponse(IncompleteRead(b"partial")), FakeResponse(b"ok")]
    with (
        patch("flat_searcher.scraper.http_client.urlopen", side_effect=attempts),
        patch("flat_searcher.scraper.http_client.time.sleep"),
    ):
        result = _client().fetch_text("https://example.com/")
    assert result.text == "ok"


def test_raises_after_exhausting_retries_on_connection_reset():
    with (
        patch(
            "flat_searcher.scraper.http_client.urlopen",
            side_effect=ConnectionResetError(10054, "connection reset"),
        ) as urlopen,
        patch("flat_searcher.scraper.http_client.time.sleep"),
        pytest.raises(FetchError),
    ):
        _client().fetch_text("https://example.com/")
    assert urlopen.call_count == 3


def test_raises_after_exhausting_retries_on_incomplete_read():
    with (
        patch(
            "flat_searcher.scraper.http_client.urlopen",
            side_effect=IncompleteRead(b"partial"),
        ) as urlopen,
        patch("flat_searcher.scraper.http_client.time.sleep"),
        pytest.raises(FetchError),
    ):
        _client().fetch_text("https://example.com/")
    assert urlopen.call_count == 3
