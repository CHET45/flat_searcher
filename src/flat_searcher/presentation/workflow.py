"""Workflow status tabs mapped to listing filters.

Tabs decide which slice of the user's workflow is visible. They are layered on
top of the user-configured base filters so that, for example, the price range a
user typed still applies inside the Favorites tab.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from flat_searcher.filtering import ListingFilters


class WorkflowTab(Enum):
    ALL = "all"
    NEW = "new"
    FAVORITES = "favorites"
    REJECTED = "rejected"
    INACTIVE = "inactive"


WORKFLOW_TAB_LABELS: dict[WorkflowTab, str] = {
    WorkflowTab.ALL: "All",
    WorkflowTab.NEW: "New",
    WorkflowTab.FAVORITES: "Favorites",
    WorkflowTab.REJECTED: "Rejected",
    WorkflowTab.INACTIVE: "Inactive",
}


def filters_for_tab(tab: WorkflowTab, base: ListingFilters) -> ListingFilters:
    """Return ``base`` adjusted for the workflow tab.

    Only the workflow-related fields are overridden; user-chosen filters such as
    price, area and district are preserved from ``base``.
    """

    if tab is WorkflowTab.ALL:
        return base
    if tab is WorkflowTab.NEW:
        return dataclasses.replace(
            base,
            hide_viewed=True,
            hide_rejected=True,
            active_only=True,
            show_inactive=False,
            favorites_only=False,
            rejected_only=False,
            inactive_only=False,
        )
    if tab is WorkflowTab.FAVORITES:
        # Favorites stay accessible even when rejected or inactive.
        return dataclasses.replace(
            base,
            favorites_only=True,
            hide_rejected=False,
            hide_viewed=False,
            active_only=False,
            show_inactive=True,
            rejected_only=False,
            inactive_only=False,
        )
    if tab is WorkflowTab.REJECTED:
        return dataclasses.replace(
            base,
            rejected_only=True,
            hide_rejected=False,
            hide_viewed=False,
            favorites_only=False,
            active_only=False,
            show_inactive=True,
            inactive_only=False,
        )
    if tab is WorkflowTab.INACTIVE:
        return dataclasses.replace(
            base,
            inactive_only=True,
            active_only=False,
            show_inactive=True,
            hide_rejected=False,
            hide_viewed=False,
            favorites_only=False,
            rejected_only=False,
        )
    raise ValueError(f"Unsupported workflow tab: {tab!r}")
