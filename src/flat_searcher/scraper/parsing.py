"""Parsing and normalization helpers for SS.com data."""

from __future__ import annotations

import hashlib
import re
from html import unescape


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unescape(value).replace("\xa0", " ")
    normalized = re.sub(r"[ \t\r\f\v]+", " ", normalized)
    normalized = re.sub(r"\n\s+", "\n", normalized)
    normalized = normalized.strip()
    return normalized or None


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s,.]*", value)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s]*(?:[,.]\d+)?", value)
    if not match:
        return None
    normalized = match.group(0).replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_price_eur(value: str | None) -> int | None:
    if not value:
        return None
    before_currency = value.split("€", 1)[0]
    return parse_int(before_currency)


def parse_price_per_m2(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\(([\d\s,.]+)\s*€\s*/\s*m", value)
    if not match:
        return None
    return parse_float(match.group(1))


def parse_floor(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    match = re.search(r"(\d+)\s*/\s*(\d+)", value)
    if match:
        return int(match.group(1)), int(match.group(2))
    return parse_int(value), None


def split_address(address: str | None) -> tuple[str | None, str | None]:
    address = clean_text(address)
    if not address:
        return None, None
    address = re.sub(r"\s*\[\s*Karte\s*\]\s*$", "", address, flags=re.IGNORECASE)
    match = re.match(r"^(?P<street>.+?)\s+(?P<number>\d[\w./-]*)$", address)
    if not match:
        return address, None
    return clean_text(match.group("street")), clean_text(match.group("number"))


def stable_hash(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
