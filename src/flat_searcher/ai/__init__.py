"""AI analysis package."""

from flat_searcher.ai.gemini_client import (
    GeminiModelClient,
    GeminiResponseError,
    GeminiSetupError,
)
from flat_searcher.ai.pipeline import (
    AIAnalysisPipeline,
    AIModelClient,
    Pass1PipelineResult,
    Pass2PipelineResult,
)
from flat_searcher.ai.prompts import ImagePromptInput, build_pass1_prompt, build_pass2_prompt
from flat_searcher.ai.schemas import (
    AIValidationError,
    ClassifiedImage,
    ImageCategory,
    LayoutConfidenceLabel,
    MortgageRiskLevel,
    Pass1ImageAnalysis,
    Pass2ListingAnalysis,
)

__all__ = [
    "AIValidationError",
    "AIAnalysisPipeline",
    "AIModelClient",
    "ClassifiedImage",
    "GeminiModelClient",
    "GeminiResponseError",
    "GeminiSetupError",
    "ImageCategory",
    "ImagePromptInput",
    "LayoutConfidenceLabel",
    "MortgageRiskLevel",
    "Pass1ImageAnalysis",
    "Pass1PipelineResult",
    "Pass2ListingAnalysis",
    "Pass2PipelineResult",
    "build_pass1_prompt",
    "build_pass2_prompt",
]
