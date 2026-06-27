"""Prompt builders for the two-pass Gemini analysis pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ImagePromptInput:
    image_id: str
    source_url: str


def build_pass1_prompt(images: tuple[ImagePromptInput, ...]) -> str:
    image_payload = [{"image_id": image.image_id, "source_url": image.source_url} for image in images]
    return "\n".join(
        [
            "Analyze apartment listing images for internal classification.",
            "Return only valid JSON. Do not include markdown.",
            "Classify every image. Do not aggressively discard interior photos.",
            "Use these categories exactly:",
            (
                "floor_plan, interior_room, kitchen, bathroom, corridor_or_hallway, "
                "exterior_building, entrance_staircase, yard_or_street_view, "
                "duplicate_or_near_duplicate, irrelevant_or_decorative, agency_collage"
            ),
            "Attached images follow the same order as the Images list below.",
            "Return fields: images, floor_plan_image_ids, images_used_for_layout, "
            "images_used_for_building_type, ignored_images, duplicate_images, image_groups_by_room.",
            "Images:",
            json.dumps(image_payload, ensure_ascii=False),
        ]
    )


def build_pass2_prompt(
    listing_text: str,
    ss_fields: dict[str, object],
    pass1_output: dict[str, object],
    layout_priors: tuple[dict[str, object], ...] = (),
) -> str:
    payload = {
        "listing_text": listing_text,
        "ss_fields": ss_fields,
        "pass1_image_analysis": pass1_output,
        "layout_priors": layout_priors,
    }
    return "\n".join(
        [
            "Analyze this Riga apartment listing for layout and mortgage suitability.",
            "Return only valid JSON. Do not include markdown.",
            "Do not trust SS.com room count blindly.",
            "Kitchen-living is not a private room.",
            "Walkthrough rooms are not counted as private rooms.",
            "If a floor plan exists, treat it as the primary layout source.",
            "Use layout_confidence_label exactly as one of: Confirmed, Likely, Unclear, Conflict.",
            "Use mortgage_risk_level exactly as one of: Low, Medium, High, Critical, Unknown.",
            "Return user-facing explanations in English.",
            "Return fields: ai_detected_living_rooms, effective_private_rooms, walkthrough_rooms, "
            "kitchen_living_detected, separate_kitchen_detected, layout_class, "
            "layout_confidence_label, ss_vs_ai_room_conflict, layout_explanation_user, "
            "floor_plan_image_ids, building_type_guess, series_guess, wooden_building_risk, "
            "stove_heating_risk, mortgage_risk_level, mortgage_risk_reasons, "
            "mortgage_explanation_user.",
            "Input:",
            json.dumps(payload, ensure_ascii=False),
        ]
    )
