"""Ranking service for visible listing candidates."""

from __future__ import annotations

from dataclasses import dataclass

from flat_searcher.filtering import ListingCandidate, ListingFilters, filter_candidates


@dataclass(frozen=True)
class RankedCandidate:
    position: int
    candidate: ListingCandidate


def rank_candidates(
    candidates: tuple[ListingCandidate, ...],
    filters: ListingFilters,
) -> tuple[RankedCandidate, ...]:
    visible = filter_candidates(candidates, filters)
    sorted_candidates = sorted(visible, key=_ranking_key)
    return tuple(
        RankedCandidate(position=index, candidate=candidate)
        for index, candidate in enumerate(sorted_candidates, start=1)
    )


def _ranking_key(candidate: ListingCandidate) -> tuple[int, float, int, int]:
    known_score = 0 if candidate.score is not None else 1
    negative_score = -(candidate.score or 0)
    price = candidate.price_eur if candidate.price_eur is not None else 10**12
    return known_score, negative_score, price, candidate.listing_id
