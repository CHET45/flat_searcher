"""JSON parsing helpers for AI responses."""

from __future__ import annotations

import json
from typing import Any

from flat_searcher.ai.schemas import AIValidationError


def parse_ai_json_object(raw_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise AIValidationError(f"Invalid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise AIValidationError("AI response must be a JSON object")
    return parsed
