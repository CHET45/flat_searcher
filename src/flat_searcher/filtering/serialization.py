"""JSON-friendly serialization for listing filters.

Search sessions persist a user's filter state, so filters must round-trip
through plain JSON. Frozensets are stored as sorted lists and rebuilt on load.
"""

from __future__ import annotations

import dataclasses

from flat_searcher.filtering.listing_filters import ListingFilters

_SET_FIELDS = {"districts", "declared_rooms", "effective_private_rooms"}


def filters_to_dict(filters: ListingFilters) -> dict[str, object]:
    data: dict[str, object] = {}
    for field in dataclasses.fields(filters):
        value = getattr(filters, field.name)
        if field.name in _SET_FIELDS:
            data[field.name] = sorted(value)
        else:
            data[field.name] = value
    return data


def filters_from_dict(data: dict[str, object]) -> ListingFilters:
    known_fields = {field.name for field in dataclasses.fields(ListingFilters)}
    kwargs: dict[str, object] = {}
    for name, value in data.items():
        if name not in known_fields:
            continue
        if name in _SET_FIELDS and value is not None:
            kwargs[name] = frozenset(value)
        else:
            kwargs[name] = value
    return ListingFilters(**kwargs)
