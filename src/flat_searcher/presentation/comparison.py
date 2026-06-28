"""Side-by-side comparison view for 2-5 apartment candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flat_searcher.ai import MortgageRiskLevel
from flat_searcher.db.read_models import ListingDetailReadModel
from flat_searcher.presentation.titles import format_apartment_title

MIN_COMPARISON = 2
MAX_COMPARISON = 5

_ValueFn = Callable[[ListingDetailReadModel], str]


@dataclass(frozen=True)
class ComparisonColumn:
    listing_id: int
    title: str


@dataclass(frozen=True)
class ComparisonRow:
    label: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class ComparisonView:
    columns: tuple[ComparisonColumn, ...]
    rows: tuple[ComparisonRow, ...]


def build_comparison_view(
    details: tuple[ListingDetailReadModel, ...],
) -> ComparisonView:
    """Build a comparison of 2-5 apartments.

    Each row is one attribute; each column is one apartment. The comparison is a
    plain read view: it never re-ranks or hides apartments.
    """

    if not MIN_COMPARISON <= len(details) <= MAX_COMPARISON:
        raise ValueError(
            f"Comparison supports between {MIN_COMPARISON} and {MAX_COMPARISON} apartments."
        )

    columns = tuple(
        ComparisonColumn(listing_id=detail.listing_id, title=_title(detail))
        for detail in details
    )
    attribute_builders: tuple[tuple[str, _ValueFn], ...] = (
        ("Price", lambda d: _price(d.price_eur)),
        ("EUR/m2", lambda d: _price_per_m2(d.price_per_m2)),
        ("Area", lambda d: _area(d.area_m2)),
        ("Effective private rooms", lambda d: _value(d.effective_private_rooms)),
        ("SS-declared rooms", lambda d: _value(d.declared_rooms_ss)),
        ("Layout confidence", lambda d: _enum(d.layout_confidence_label)),
        ("Mortgage risk", lambda d: _enum(d.mortgage_risk_level)),
        ("RTU distance", lambda d: _distance(d.distance_to_rtu_m)),
        ("Transport score", lambda d: _score(d.transport_score)),
        ("Central station distance", lambda d: _distance(d.distance_to_central_station_m)),
        ("Shops within 1200 m", lambda d: _value(d.shops_within_1200m)),
        ("Building / series", lambda d: d.building_series or d.building_type or "unknown"),
        ("Score", lambda d: _score(d.overall_score)),
        ("Flags", lambda d: ", ".join(comparison_flags(d)) or "none"),
    )
    rows = tuple(
        ComparisonRow(
            label=label,
            values=tuple(builder(detail) for detail in details),
        )
        for label, builder in attribute_builders
    )
    return ComparisonView(columns=columns, rows=rows)


def comparison_flags(detail: ListingDetailReadModel) -> tuple[str, ...]:
    flags: list[str] = []
    if detail.is_favorite:
        flags.append("Favorite")
    if (
        detail.effective_private_rooms is not None
        and detail.declared_rooms_ss is not None
        and detail.effective_private_rooms != detail.declared_rooms_ss
    ):
        flags.append("Room conflict")
    if detail.kitchen_living_detected:
        flags.append("Kitchen-living")
    if detail.mortgage_risk_level in {MortgageRiskLevel.HIGH, MortgageRiskLevel.CRITICAL}:
        flags.append("High mortgage risk")
    return tuple(flags)


def _title(detail: ListingDetailReadModel) -> str:
    return format_apartment_title(
        district=detail.district,
        street=detail.street,
        effective_private_rooms=detail.effective_private_rooms,
        declared_rooms_ss=detail.declared_rooms_ss,
        area_m2=detail.area_m2,
        price_eur=detail.price_eur,
        kitchen_living_detected=detail.kitchen_living_detected,
    )


def _price(price_eur: int | None) -> str:
    if price_eur is None:
        return "unknown"
    return f"{price_eur:,}".replace(",", " ") + " EUR"


def _price_per_m2(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:,.2f}".replace(",", " ") + " EUR/m2"


def _area(area_m2: float | None) -> str:
    if area_m2 is None:
        return "unknown"
    value = float(area_m2)
    return f"{int(value)} m2" if value.is_integer() else f"{value:.1f} m2"


def _distance(distance_m: float | None) -> str:
    return "unknown" if distance_m is None else f"{distance_m:.0f} m"


def _score(score: float | None) -> str:
    return "unknown" if score is None else f"{score:.1f}"


def _value(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _enum(value: object | None) -> str:
    return "unknown" if value is None else getattr(value, "value", str(value))
