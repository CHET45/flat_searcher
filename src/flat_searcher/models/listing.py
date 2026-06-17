"""Typed listing data used between scraper, storage and analysis layers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ListingSummary:
    ss_id: str
    ss_url: str
    title: str | None = None
    district: str | None = None
    street: str | None = None
    declared_rooms_ss: int | None = None
    area_m2: float | None = None
    floor: int | None = None
    total_floors: int | None = None
    building_series: str | None = None
    price_eur: int | None = None
    price_per_m2: float | None = None
    table_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ListingDetail:
    ss_url: str
    description_text: str | None = None
    address_raw: str | None = None
    district: str | None = None
    street: str | None = None
    house_number: str | None = None
    price_eur: int | None = None
    price_per_m2: float | None = None
    area_m2: float | None = None
    declared_rooms_ss: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    building_series: str | None = None
    building_type: str | None = None
    listing_date_text: str | None = None
    unique_visits: int | None = None
    image_urls: tuple[str, ...] = ()
    detail_fields: dict[str, str] = field(default_factory=dict)
    raw_text_snapshot: str | None = None
    raw_html: str | None = None


@dataclass(frozen=True)
class ListingPayload:
    ss_id: str
    ss_url: str
    listing_title: str | None = None
    listing_summary_text: str | None = None
    listing_table_metadata: dict[str, str] = field(default_factory=dict)
    detail_fields: dict[str, str] = field(default_factory=dict)

    address_raw: str | None = None
    district: str | None = None
    street: str | None = None
    house_number: str | None = None

    price_eur: int | None = None
    price_per_m2: float | None = None
    area_m2: float | None = None
    declared_rooms_ss: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    building_series: str | None = None
    building_type: str | None = None

    listing_date_text: str | None = None
    unique_visits: int | None = None
    description_text: str | None = None
    image_urls: tuple[str, ...] = ()
    raw_text_snapshot: str | None = None
    raw_html: str | None = None
