"""Apartment title formatting."""

from __future__ import annotations


def format_apartment_title(
    district: str | None,
    street: str | None,
    effective_private_rooms: int | None,
    declared_rooms_ss: int | None,
    area_m2: float | None,
    price_eur: int | None,
    kitchen_living_detected: bool = False,
) -> str:
    parts = [
        district or "Unknown district",
        street or "Unknown street",
        f"AI: {format_ai_room_label(effective_private_rooms, kitchen_living_detected)} / SS: {_value_or_unknown(declared_rooms_ss)}",
        _format_area(area_m2),
        _format_price(price_eur),
    ]
    return " - ".join(parts)


def format_ai_room_label(
    effective_private_rooms: int | None,
    kitchen_living_detected: bool = False,
) -> str:
    if effective_private_rooms is None:
        return "unclear"
    label = f"{effective_private_rooms} private"
    if kitchen_living_detected:
        return f"{label} + kitchen-living"
    return label


def _format_area(area_m2: float | None) -> str:
    if area_m2 is None:
        return "area unknown"
    value = float(area_m2)
    if value.is_integer():
        return f"{int(value)} m2"
    return f"{value:.1f} m2"


def _format_price(price_eur: int | None) -> str:
    if price_eur is None:
        return "price unknown"
    return f"{price_eur:,}".replace(",", " ") + " EUR"


def _value_or_unknown(value: int | None) -> str:
    return "unknown" if value is None else str(value)
