"""Two-pass AI analysis pipeline over an abstract model client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from flat_searcher.ai.json_io import parse_ai_json_object
from flat_searcher.ai.prompts import ImagePromptInput, build_pass1_prompt, build_pass2_prompt
from flat_searcher.ai.response_schemas import PASS1_RESPONSE_SCHEMA, PASS2_RESPONSE_SCHEMA
from flat_searcher.ai.schemas import Pass1ImageAnalysis, Pass2ListingAnalysis


class AIModelClient(Protocol):
    def generate_text(
        self,
        prompt: str,
        image_paths: tuple[Path, ...] = (),
        response_schema: dict[str, object] | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class Pass1PipelineResult:
    raw_json: str
    analysis: Pass1ImageAnalysis


@dataclass(frozen=True)
class Pass2PipelineResult:
    raw_json: str
    analysis: Pass2ListingAnalysis


class AIAnalysisPipeline:
    def __init__(self, model_client: AIModelClient) -> None:
        self.model_client = model_client

    def analyze_images(
        self,
        images: tuple[ImagePromptInput, ...],
        image_paths: tuple[Path, ...],
    ) -> Pass1PipelineResult:
        prompt = build_pass1_prompt(images)
        raw_json = self.model_client.generate_text(
            prompt,
            image_paths=image_paths,
            response_schema=PASS1_RESPONSE_SCHEMA,
        )
        analysis = Pass1ImageAnalysis.from_dict(parse_ai_json_object(raw_json))
        return Pass1PipelineResult(raw_json=raw_json, analysis=analysis)

    def analyze_listing(
        self,
        listing_text: str,
        ss_fields: dict[str, object],
        pass1_output: dict[str, object],
        image_paths: tuple[Path, ...],
        layout_priors: tuple[dict[str, object], ...] = (),
    ) -> Pass2PipelineResult:
        prompt = build_pass2_prompt(
            listing_text=listing_text,
            ss_fields=ss_fields,
            pass1_output=pass1_output,
            layout_priors=layout_priors,
        )
        raw_json = self.model_client.generate_text(
            prompt,
            image_paths=image_paths,
            response_schema=PASS2_RESPONSE_SCHEMA,
        )
        analysis = Pass2ListingAnalysis.from_dict(parse_ai_json_object(raw_json))
        return Pass2PipelineResult(raw_json=raw_json, analysis=analysis)
