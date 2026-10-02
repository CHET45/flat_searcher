"""Local Ollama chat client for judging; the native API is the only one that can disable thinking."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flat_searcher.judging.errors import PromptTooLargeError

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"
# 12k keeps a 9B model almost entirely on an 8 GB GPU; the prompt with eight images is ~9k.
DEFAULT_NUM_CTX = 12288
# A valid verdict is 350-500 tokens; the cap turns a loop into a fast failure,
# and the repeat penalty makes loops rare to begin with.
NUM_PREDICT = 1200


class OllamaError(RuntimeError):
    pass


class OllamaJudgeClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        *,
        num_ctx: int = DEFAULT_NUM_CTX,
        timeout_seconds: float = 900.0,
        keep_alive: str = "30m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def available(self) -> bool:
        try:
            with urlopen(f"{self.base_url}/api/version", timeout=3.0) as response:
                return response.status == 200
        except (OSError, HTTPError, URLError):
            return False

    def ask(
        self,
        system: str,
        text: str,
        images_b64: Sequence[str],
        schema: Mapping[str, Any],
    ) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text, "images": list(images_b64)},
            ],
            "think": False,
            "format": dict(schema),
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0.1,
                "num_ctx": self.num_ctx,
                "num_predict": NUM_PREDICT,
                "repeat_penalty": 1.15,
                "repeat_last_n": 256,
            },
        }
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            if error.code == 400 and "context size" in detail:
                raise PromptTooLargeError(detail) from error
            raise OllamaError(f"Ollama returned HTTP {error.code}: {detail}") from error
        except (OSError, URLError) as error:
            raise OllamaError(f"Ollama unreachable: {type(error).__name__}") from error
        content = (payload.get("message") or {}).get("content")
        if not content:
            raise OllamaError("Ollama returned an empty message")
        # A prompt that fits but leaves no room to answer is not rejected by
        # Ollama; the answer is simply cut when the context fills up.
        if payload.get("done_reason") == "length" and int(payload.get("eval_count") or 0) < NUM_PREDICT:
            raise PromptTooLargeError(f"context exhausted after {payload.get('eval_count')} output tokens")
        return content
