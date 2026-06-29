"""Structured-output response schemas for the two-pass Gemini pipeline.

These are provider-agnostic OpenAPI-style schema dictionaries (the subset that
Gemini controlled generation accepts). They are passed to the model as
``response_schema`` so the model is forced to emit every required field with a
valid value, instead of occasionally omitting fields such as image ``category``.

Field names and enum values mirror :mod:`flat_searcher.ai.schemas`.
"""

from __future__ import annotations

from flat_searcher.ai.schemas import (
    ImageCategory,
    LayoutConfidenceLabel,
    MortgageRiskLevel,
)

_IMAGE_CATEGORY_VALUES = [item.value for item in ImageCategory]
_LAYOUT_CONFIDENCE_VALUES = [item.value for item in LayoutConfidenceLabel]
_MORTGAGE_RISK_VALUES = [item.value for item in MortgageRiskLevel]

_STRING_ARRAY = {"type": "ARRAY", "items": {"type": "STRING"}}


# Gemini controlled generation does not support free-form maps (objects with
# arbitrary keys), so image_groups_by_room is expressed as an array of
# {room, image_ids} pairs. The parser accepts both this form and a plain object.
PASS1_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "properties": {
        "images": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "image_id": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": _IMAGE_CATEGORY_VALUES},
                    "useful_for_layout": {"type": "BOOLEAN"},
                    "useful_for_building_type": {"type": "BOOLEAN"},
                    "duplicate_of_image_id": {"type": "STRING", "nullable": True},
                    "likely_room_group": {"type": "STRING", "nullable": True},
                    "explanation": {"type": "STRING", "nullable": True},
                },
                "required": ["image_id", "category"],
            },
        },
        "floor_plan_image_ids": _STRING_ARRAY,
        "images_used_for_layout": _STRING_ARRAY,
        "images_used_for_building_type": _STRING_ARRAY,
        "ignored_images": _STRING_ARRAY,
        "duplicate_images": _STRING_ARRAY,
        "image_groups_by_room": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "room": {"type": "STRING"},
                    "image_ids": _STRING_ARRAY,
                },
                "required": ["room", "image_ids"],
            },
        },
    },
    "required": ["images"],
}


PASS2_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "properties": {
        "ai_detected_living_rooms": {"type": "INTEGER", "nullable": True},
        "effective_private_rooms": {"type": "INTEGER", "nullable": True},
        "walkthrough_rooms": {"type": "INTEGER", "nullable": True},
        "kitchen_living_detected": {"type": "BOOLEAN"},
        "separate_kitchen_detected": {"type": "BOOLEAN"},
        "layout_class": {"type": "STRING", "nullable": True},
        "layout_confidence_label": {
            "type": "STRING",
            "enum": _LAYOUT_CONFIDENCE_VALUES,
        },
        "ss_vs_ai_room_conflict": {"type": "BOOLEAN"},
        "layout_explanation_user": {"type": "STRING"},
        "floor_plan_image_ids": _STRING_ARRAY,
        "building_type_guess": {"type": "STRING", "nullable": True},
        "series_guess": {"type": "STRING", "nullable": True},
        "wooden_building_risk": {"type": "BOOLEAN"},
        "stove_heating_risk": {"type": "BOOLEAN"},
        "mortgage_risk_level": {"type": "STRING", "enum": _MORTGAGE_RISK_VALUES},
        "mortgage_risk_reasons": _STRING_ARRAY,
        "mortgage_explanation_user": {"type": "STRING"},
    },
    "required": [
        "layout_confidence_label",
        "layout_explanation_user",
        "mortgage_risk_level",
        "mortgage_explanation_user",
    ],
}
