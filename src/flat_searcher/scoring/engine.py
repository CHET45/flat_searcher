"""Weighted scoring engine."""

from __future__ import annotations

from dataclasses import dataclass

from flat_searcher.scoring.profiles import ScoreBlockKey, ScoringProfile


@dataclass(frozen=True)
class BlockScore:
    block_key: ScoreBlockKey
    score: float | None
    explanation: str | None = None


@dataclass(frozen=True)
class ScorePenalty:
    key: str
    points: float
    explanation: str


@dataclass(frozen=True)
class ScoreResult:
    profile_key: str
    overall_score: float | None
    block_scores: tuple[BlockScore, ...]
    penalties: tuple[ScorePenalty, ...]
    explanation: str


def calculate_weighted_score(
    profile: ScoringProfile,
    block_scores: tuple[BlockScore, ...],
    penalties: tuple[ScorePenalty, ...] = (),
) -> ScoreResult:
    weighted_total = 0.0
    weight_total = 0

    for block_score in block_scores:
        if block_score.score is None:
            continue
        weight = profile.weight_for(block_score.block_key)
        if weight <= 0:
            continue
        weighted_total += _clamp_score(block_score.score) * weight
        weight_total += weight

    if weight_total == 0:
        return ScoreResult(
            profile_key=profile.key,
            overall_score=None,
            block_scores=block_scores,
            penalties=penalties,
            explanation="No enabled scoring blocks have values.",
        )

    penalty_total = sum(max(0.0, penalty.points) for penalty in penalties)
    overall_score = max(0.0, weighted_total / weight_total - penalty_total)
    return ScoreResult(
        profile_key=profile.key,
        overall_score=round(overall_score, 2),
        block_scores=block_scores,
        penalties=penalties,
        explanation=f"Weighted score calculated from {weight_total} total profile weight.",
    )


def _clamp_score(score: float) -> float:
    return min(100.0, max(0.0, score))
