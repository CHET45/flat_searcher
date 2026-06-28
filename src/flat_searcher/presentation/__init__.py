"""Presentation helpers shared by UI surfaces."""

from flat_searcher.presentation.comparison import (
    MAX_COMPARISON,
    MIN_COMPARISON,
    ComparisonColumn,
    ComparisonRow,
    ComparisonView,
    build_comparison_view,
    comparison_flags,
)
from flat_searcher.presentation.titles import format_apartment_title, format_ai_room_label
from flat_searcher.presentation.view_models import (
    DetailViewModel,
    RankingRowViewModel,
    detail_view_model,
    key_flags,
    ranking_row_view_model,
)
from flat_searcher.presentation.workflow import (
    WORKFLOW_TAB_LABELS,
    WorkflowTab,
    filters_for_tab,
)

__all__ = [
    "ComparisonColumn",
    "ComparisonRow",
    "ComparisonView",
    "DetailViewModel",
    "MAX_COMPARISON",
    "MIN_COMPARISON",
    "RankingRowViewModel",
    "WORKFLOW_TAB_LABELS",
    "WorkflowTab",
    "build_comparison_view",
    "comparison_flags",
    "detail_view_model",
    "filters_for_tab",
    "format_ai_room_label",
    "format_apartment_title",
    "key_flags",
    "ranking_row_view_model",
]
