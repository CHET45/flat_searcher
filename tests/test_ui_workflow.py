"""Tests for UI workflow tab filters and ranking-row presentation helpers."""

from __future__ import annotations

from flat_searcher.ai import LayoutConfidenceLabel, MortgageRiskLevel
from flat_searcher.filtering import ListingCandidate, ListingFilters, filter_candidates
from flat_searcher.presentation import (
    WorkflowTab,
    filters_for_tab,
    key_flags,
    ranking_row_view_model,
)
from flat_searcher.ranking import rank_candidates


def _candidate(listing_id: int, **overrides) -> ListingCandidate:
    base = dict(listing_id=listing_id, score=50.0)
    base.update(overrides)
    return ListingCandidate(**base)


def _dataset() -> tuple[ListingCandidate, ...]:
    return (
        _candidate(1, is_viewed=False),
        _candidate(2, is_viewed=True),
        _candidate(3, is_favorite=True, is_viewed=True),
        _candidate(4, is_rejected=True),
        _candidate(5, listing_status="inactive"),
        _candidate(6, is_favorite=True, listing_status="inactive"),
    )


def _visible_ids(tab: WorkflowTab, base: ListingFilters | None = None) -> set[int]:
    filters = filters_for_tab(tab, base or ListingFilters())
    visible = filter_candidates(_dataset(), filters)
    return {candidate.listing_id for candidate in visible}


def test_all_tab_hides_rejected_and_inactive_by_default() -> None:
    assert _visible_ids(WorkflowTab.ALL) == {1, 2, 3}


def test_new_tab_only_unviewed_active() -> None:
    assert _visible_ids(WorkflowTab.NEW) == {1}


def test_favorites_tab_includes_inactive_favorites() -> None:
    assert _visible_ids(WorkflowTab.FAVORITES) == {3, 6}


def test_rejected_tab_shows_only_rejected() -> None:
    assert _visible_ids(WorkflowTab.REJECTED) == {4}


def test_inactive_tab_shows_only_inactive() -> None:
    assert _visible_ids(WorkflowTab.INACTIVE) == {5, 6}


def test_tab_preserves_user_base_filters() -> None:
    base = ListingFilters(price_max=100)
    dataset = (
        _candidate(1, price_eur=90),
        _candidate(2, price_eur=200),
    )
    filters = filters_for_tab(WorkflowTab.NEW, base)
    visible = {c.listing_id for c in filter_candidates(dataset, filters)}
    assert visible == {1}


def test_key_flags_collects_important_signals() -> None:
    candidate = _candidate(
        1,
        is_favorite=True,
        room_conflict=True,
        kitchen_living_detected=True,
        has_floor_plan=True,
        layout_confidence_label=LayoutConfidenceLabel.CONFIRMED,
        mortgage_risk_level=MortgageRiskLevel.HIGH,
        stove_heating_risk=True,
        wooden_building_risk=True,
    )
    flags = key_flags(candidate)
    assert "Favorite" in flags
    assert "Room conflict" in flags
    assert "Layout confirmed" in flags
    assert "Floor plan" in flags
    assert "Kitchen-living" in flags
    assert "High mortgage risk" in flags
    assert "Stove heating" in flags
    assert "Wooden building" in flags


def test_key_flags_empty_for_plain_candidate() -> None:
    assert key_flags(_candidate(1)) == ()


def test_ranking_row_derives_price_per_m2() -> None:
    candidate = _candidate(1, price_eur=100_000, area_m2=50.0)
    ranked = rank_candidates((candidate,), ListingFilters())
    row = ranking_row_view_model(ranked[0])
    assert row.price_per_m2_text == "2 000.00 EUR/m2"


def test_ranking_row_price_per_m2_unknown_without_area() -> None:
    candidate = _candidate(1, price_eur=100_000, area_m2=None)
    ranked = rank_candidates((candidate,), ListingFilters())
    row = ranking_row_view_model(ranked[0])
    assert row.price_per_m2_text == "unknown"
