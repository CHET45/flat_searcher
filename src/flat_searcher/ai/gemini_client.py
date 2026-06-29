"""Google Gemini implementation of the abstract AI model client."""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path


class GeminiSetupError(RuntimeError):
    pass


class GeminiResponseError(RuntimeError):
    pass


class GeminiModelClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_attempts: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        if not api_key:
            raise GeminiSetupError("GEMINI_API_KEY is required for Gemini analysis.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise GeminiSetupError(
                "Gemini support requires the optional AI dependencies. "
                "Install the project with: pip install -e .[ai]"
            ) from error

        self.client = genai.Client(api_key=api_key)
        self.types = types
        self.model = model
        self.max_attempts = max(1, max_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)

    def generate_text(
        self,
        prompt: str,
        image_paths: tuple[Path, ...] = (),
        response_schema: dict[str, object] | None = None,
    ) -> str:
        contents = []
        for image_path in image_paths:
            image_bytes = image_path.read_bytes()
            contents.append(
                self.types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=_detect_image_mime_type(image_path, image_bytes),
                )
            )
        contents.append(prompt)

        config_kwargs: dict[str, object] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }
        if response_schema is not None:
            config_kwargs["response_schema"] = response_schema

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self.types.GenerateContentConfig(**config_kwargs),
                )
                if not response.text:
                    raise GeminiResponseError("Gemini returned an empty response.")
                return response.text
            except Exception as error:
                last_error = error
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay_seconds * attempt)

        raise GeminiResponseError(
            f"Gemini request failed after {self.max_attempts} attempts: {last_error}"
        ) from last_error


def _detect_image_mime_type(path: Path, content: bytes) -> str:
    guessed_type, _ = mimetypes.guess_type(path.name)
    if guessed_type in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }:
        return guessed_type
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise GeminiSetupError(f"Unsupported image format: {path}")
