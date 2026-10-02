"""Deterministic extraction of sentences the model must not miss."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Land tenure decides mortgageability and hides in Latvian free text.
_LAND_TENURE = re.compile(
    r"\b(zem[ei]s?\b[^.!?\n]{0,60}\bnom[aāu]\w*|nom[aāu]\w*\b[^.!?\n]{0,60}\bzem[ei]s?\b"
    r"|zem[ei]s?\b[^.!?\n]{0,40}\bīpašum\w*|denacionaliz\w*)",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
MAX_HINT_CHARS = 240


def land_tenure_sentences(record: Mapping[str, Any]) -> list[str]:
    text: Mapping[str, Any] = record.get("text") or {}
    fields: Mapping[str, Any] = record.get("fields") or {}
    sources = [str(text.get("description") or ""), *(str(value) for value in fields.values())]
    found: list[str] = []
    for source in sources:
        for sentence in _SENTENCE.findall(source):
            cleaned = " ".join(sentence.split())
            match = _LAND_TENURE.search(cleaned)
            if not match:
                continue
            if len(cleaned) > MAX_HINT_CHARS:
                # A run-on paragraph with no full stop: keep the window around the phrase.
                start = max(0, match.start() - MAX_HINT_CHARS // 2)
                cleaned = "…" + cleaned[start : start + MAX_HINT_CHARS].strip() + "…"
            if cleaned not in found:
                found.append(cleaned)
    return found[:4]


def hints_for(record: Mapping[str, Any]) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    tenure = land_tenure_sentences(record)
    if tenure:
        hints["land_tenure"] = tenure
    return hints
