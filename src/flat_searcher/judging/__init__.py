"""Judging a queue with a local model."""

from flat_searcher.judging.ollama import OllamaJudgeClient
from flat_searcher.judging.runner import JudgeFilters, JudgeOptions, JudgeRun
from flat_searcher.judging.schema import (
    VERDICT_SCHEMA,
    VerdictError,
    finalize_verdict,
    price_assessment,
)

__all__ = [
    "VERDICT_SCHEMA",
    "JudgeFilters",
    "JudgeOptions",
    "JudgeRun",
    "OllamaJudgeClient",
    "VerdictError",
    "finalize_verdict",
    "price_assessment",
]
