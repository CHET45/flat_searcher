"""Local database of typical building layouts.

These priors are hypotheses, not truth. They are passed to the Gemini Pass 2
prompt as supporting context so the model can reason about likely layouts, but
real evidence (a floor plan, interior photos, listing text) always overrides
them. The application never lets a prior set the final layout conclusion.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutPrior:
    series_name: str | None
    building_type: str | None
    construction_period: str | None
    typical_area_min: float | None
    typical_area_max: float | None
    typical_room_count: int | None
    typical_layout_variants: tuple[str, ...]
    walkthrough_probability: float | None
    isolated_rooms_probability: float | None
    source_note: str | None
    confidence: str = "medium"
    verified: bool = False

    def to_prompt_dict(self) -> dict[str, object]:
        """Compact, model-friendly summary used as a hypothesis in Pass 2."""

        return {
            "series": self.series_name,
            "building_type": self.building_type,
            "construction_period": self.construction_period,
            "typical_area_range_m2": _format_range(
                self.typical_area_min, self.typical_area_max
            ),
            "typical_room_count": self.typical_room_count,
            "typical_layout_variants": list(self.typical_layout_variants),
            "walkthrough_probability": self.walkthrough_probability,
            "isolated_rooms_probability": self.isolated_rooms_probability,
            "confidence": self.confidence,
            "note": "Hypothesis only. Real listing evidence overrides this prior.",
        }


class LayoutPriorRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM layout_priors").fetchone()[0])

    def upsert_prior(self, prior: LayoutPrior) -> None:
        self.connection.execute(
            """
            INSERT INTO layout_priors (
                series_name, building_type, construction_period,
                typical_area_min, typical_area_max, typical_room_count,
                typical_layout_variants, walkthrough_probability,
                isolated_rooms_probability, source_note, confidence, verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_name, building_type, typical_room_count) DO UPDATE SET
                construction_period = excluded.construction_period,
                typical_area_min = excluded.typical_area_min,
                typical_area_max = excluded.typical_area_max,
                typical_layout_variants = excluded.typical_layout_variants,
                walkthrough_probability = excluded.walkthrough_probability,
                isolated_rooms_probability = excluded.isolated_rooms_probability,
                source_note = excluded.source_note,
                confidence = excluded.confidence,
                verified = excluded.verified
            """,
            (
                prior.series_name,
                prior.building_type,
                prior.construction_period,
                prior.typical_area_min,
                prior.typical_area_max,
                prior.typical_room_count,
                json.dumps(list(prior.typical_layout_variants), ensure_ascii=False),
                prior.walkthrough_probability,
                prior.isolated_rooms_probability,
                prior.source_note,
                prior.confidence,
                1 if prior.verified else 0,
            ),
        )

    def seed_default_priors(self) -> int:
        """Insert the bundled starter priors if the table is empty.

        Returns the number of priors written. Existing data is left untouched so
        user-curated priors are never overwritten by the defaults.
        """

        if self.count() > 0:
            return 0
        for prior in _DEFAULT_PRIORS:
            self.upsert_prior(prior)
        return len(_DEFAULT_PRIORS)

    def find_candidates(
        self,
        series_name: str | None,
        building_type: str | None,
        area_m2: float | None,
        room_count: int | None,
        limit: int = 6,
    ) -> tuple[LayoutPrior, ...]:
        """Return up to ``limit`` priors most relevant to the listing features.

        Relevance is scored in Python over the (small) prior table: series and
        building-type matches weigh most, with room count and area range as
        secondary signals. Priors with no signal are excluded.
        """

        priors = tuple(_prior_from_row(row) for row in self._all_rows())
        scored = [
            (score, prior)
            for prior in priors
            if (score := _relevance(prior, series_name, building_type, area_m2, room_count)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(prior for _score, prior in scored[:limit])

    def _all_rows(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM layout_priors").fetchall()


def _relevance(
    prior: LayoutPrior,
    series_name: str | None,
    building_type: str | None,
    area_m2: float | None,
    room_count: int | None,
) -> int:
    score = 0
    if series_name and prior.series_name and _norm(series_name) in _norm(prior.series_name):
        score += 5
    elif series_name and prior.series_name and _norm(prior.series_name) in _norm(series_name):
        score += 5
    if (
        building_type
        and prior.building_type
        and _norm(building_type) in _norm(prior.building_type)
    ):
        score += 3
    if room_count is not None and prior.typical_room_count == room_count:
        score += 2
    if (
        area_m2 is not None
        and prior.typical_area_min is not None
        and prior.typical_area_max is not None
        and prior.typical_area_min <= area_m2 <= prior.typical_area_max
    ):
        score += 2
    return score


def _norm(value: str) -> str:
    return value.strip().lower()


def _format_range(minimum: float | None, maximum: float | None) -> str | None:
    if minimum is None and maximum is None:
        return None
    if minimum is None:
        return f"up to {maximum:.0f}"
    if maximum is None:
        return f"from {minimum:.0f}"
    return f"{minimum:.0f}-{maximum:.0f}"


def _prior_from_row(row: sqlite3.Row) -> LayoutPrior:
    return LayoutPrior(
        series_name=row["series_name"],
        building_type=row["building_type"],
        construction_period=row["construction_period"],
        typical_area_min=row["typical_area_min"],
        typical_area_max=row["typical_area_max"],
        typical_room_count=row["typical_room_count"],
        typical_layout_variants=tuple(json.loads(row["typical_layout_variants"] or "[]")),
        walkthrough_probability=row["walkthrough_probability"],
        isolated_rooms_probability=row["isolated_rooms_probability"],
        source_note=row["source_note"],
        confidence=row["confidence"],
        verified=bool(row["verified"]),
    )


# Bundled starter priors for common Riga building stock. These are conservative
# hypotheses sourced from general knowledge of Soviet-era series and Riga housing;
# they are unverified and meant only to give Gemini plausible layout candidates.
_DEFAULT_PRIORS: tuple[LayoutPrior, ...] = (
    LayoutPrior(
        series_name="467",
        building_type="panel",
        construction_period="1960s-1970s",
        typical_area_min=30.0,
        typical_area_max=48.0,
        typical_room_count=2,
        typical_layout_variants=("walkthrough living room", "small isolated bedroom"),
        walkthrough_probability=0.7,
        isolated_rooms_probability=0.3,
        source_note="Khrushchyovka-style series; walkthrough rooms common.",
        confidence="medium",
    ),
    LayoutPrior(
        series_name="602",
        building_type="panel",
        construction_period="1980s",
        typical_area_min=45.0,
        typical_area_max=65.0,
        typical_room_count=3,
        typical_layout_variants=("isolated rooms", "separate kitchen"),
        walkthrough_probability=0.2,
        isolated_rooms_probability=0.8,
        source_note="Improved late-Soviet series; mostly isolated rooms.",
        confidence="medium",
    ),
    LayoutPrior(
        series_name="119",
        building_type="panel",
        construction_period="1980s",
        typical_area_min=50.0,
        typical_area_max=70.0,
        typical_room_count=3,
        typical_layout_variants=("isolated rooms", "larger kitchen"),
        walkthrough_probability=0.25,
        isolated_rooms_probability=0.75,
        source_note="Improved series with larger kitchens.",
        confidence="medium",
    ),
    LayoutPrior(
        series_name="103",
        building_type="panel",
        construction_period="1970s-1980s",
        typical_area_min=40.0,
        typical_area_max=60.0,
        typical_room_count=2,
        typical_layout_variants=("isolated bedroom", "kitchen-living possible"),
        walkthrough_probability=0.3,
        isolated_rooms_probability=0.7,
        source_note="Common Riga panel series.",
        confidence="medium",
    ),
    LayoutPrior(
        series_name="lt",
        building_type="panel",
        construction_period="1970s-1980s",
        typical_area_min=45.0,
        typical_area_max=70.0,
        typical_room_count=3,
        typical_layout_variants=("isolated rooms", "Lithuanian project layout"),
        walkthrough_probability=0.25,
        isolated_rooms_probability=0.75,
        source_note="Lithuanian-project large-panel series.",
        confidence="medium",
    ),
    LayoutPrior(
        series_name="stalinka",
        building_type="brick",
        construction_period="1940s-1950s",
        typical_area_min=45.0,
        typical_area_max=90.0,
        typical_room_count=3,
        typical_layout_variants=("high ceilings", "isolated rooms", "some walkthrough"),
        walkthrough_probability=0.4,
        isolated_rooms_probability=0.6,
        source_note="Stalin-era brick buildings; varied layouts.",
        confidence="low",
    ),
    LayoutPrior(
        series_name="pre-war",
        building_type="wooden",
        construction_period="pre-1940",
        typical_area_min=30.0,
        typical_area_max=80.0,
        typical_room_count=2,
        typical_layout_variants=("walkthrough rooms common", "stove heating possible"),
        walkthrough_probability=0.6,
        isolated_rooms_probability=0.4,
        source_note="Pre-war wooden Riga housing; mortgage risk likely.",
        confidence="low",
    ),
    LayoutPrior(
        series_name="new project",
        building_type="monolith",
        construction_period="2000s-present",
        typical_area_min=40.0,
        typical_area_max=120.0,
        typical_room_count=3,
        typical_layout_variants=("isolated rooms", "open kitchen-living common"),
        walkthrough_probability=0.1,
        isolated_rooms_probability=0.9,
        source_note="Modern projects; open kitchen-living frequent.",
        confidence="medium",
    ),
)
