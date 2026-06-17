"""Presentation helpers shared by UI surfaces."""

from flat_searcher.presentation.titles import format_apartment_title, format_ai_room_label
from flat_searcher.presentation.view_models import (
    DetailViewModel,
    RankingRowViewModel,
    detail_view_model,
    ranking_row_view_model,
)

__all__ = [
    "DetailViewModel",
    "RankingRowViewModel",
    "detail_view_model",
    "format_ai_room_label",
    "format_apartment_title",
    "ranking_row_view_model",
]
