"""Validated internal AI output schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AIValidationError(ValueError):
    pass


class ImageCategory(StrEnum):
    FLOOR_PLAN = "floor_plan"
    INTERIOR_ROOM = "interior_room"
    KITCHEN = "kitchen"
    BATHROOM = "bathroom"
    CORRIDOR_OR_HALLWAY = "corridor_or_hallway"
    EXTERIOR_BUILDING = "exterior_building"
    ENTRANCE_STAIRCASE = "entrance_staircase"
    YARD_OR_STREET_VIEW = "yard_or_street_view"
    DUPLICATE_OR_NEAR_DUPLICATE = "duplicate_or_near_duplicate"
    IRRELEVANT_OR_DECORATIVE = "irrelevant_or_decorative"
    AGENCY_COLLAGE = "agency_collage"


class LayoutConfidenceLabel(StrEnum):
    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    UNCLEAR = "Unclear"
    CONFLICT = "Conflict"


class MortgageRiskLevel(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class ClassifiedImage:
    image_id: str
    category: ImageCategory
    useful_for_layout: bool = False
    useful_for_building_type: bool = False
    duplicate_of_image_id: str | None = None
    likely_room_group: str | None = None
    explanation: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClassifiedImage":
        return cls(
            image_id=_required_str(data, "image_id"),
            category=_enum_value(ImageCategory, data.get("category"), "category"),
            useful_for_layout=bool(data.get("useful_for_layout", False)),
            useful_for_building_type=bool(data.get("useful_for_building_type", False)),
            duplicate_of_image_id=_optional_str(data, "duplicate_of_image_id"),
            likely_room_group=_optional_str(data, "likely_room_group"),
            explanation=_optional_str(data, "explanation"),
        )


@dataclass(frozen=True)
class Pass1ImageAnalysis:
    images: tuple[ClassifiedImage, ...]
    floor_plan_image_ids: tuple[str, ...] = ()
    images_used_for_layout: tuple[str, ...] = ()
    images_used_for_building_type: tuple[str, ...] = ()
    ignored_images: tuple[str, ...] = ()
    duplicate_images: tuple[str, ...] = ()
    image_groups_by_room: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pass1ImageAnalysis":
        images_data = _required_list(data, "images")
        return cls(
            images=tuple(ClassifiedImage.from_dict(item) for item in images_data),
            floor_plan_image_ids=_str_tuple(data.get("floor_plan_image_ids", ())),
            images_used_for_layout=_str_tuple(data.get("images_used_for_layout", ())),
            images_used_for_building_type=_str_tuple(
                data.get("images_used_for_building_type", ())
            ),
            ignored_images=_str_tuple(data.get("ignored_images", ())),
            duplicate_images=_str_tuple(data.get("duplicate_images", ())),
            image_groups_by_room=_room_groups(data.get("image_groups_by_room", {})),
        )


@dataclass(frozen=True)
class Pass2ListingAnalysis:
    ai_detected_living_rooms: int | None
    effective_private_rooms: int | None
    walkthrough_rooms: int | None
    kitchen_living_detected: bool
    separate_kitchen_detected: bool
    layout_class: str | None
    layout_confidence_label: LayoutConfidenceLabel
    ss_vs_ai_room_conflict: bool
    layout_explanation_user: str
    floor_plan_image_ids: tuple[str, ...]

    building_type_guess: str | None
    series_guess: str | None
    wooden_building_risk: bool
    stove_heating_risk: bool
    mortgage_risk_level: MortgageRiskLevel
    mortgage_risk_reasons: tuple[str, ...]
    mortgage_explanation_user: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pass2ListingAnalysis":
        return cls(
            ai_detected_living_rooms=_optional_int(data, "ai_detected_living_rooms"),
            effective_private_rooms=_optional_int(data, "effective_private_rooms"),
            walkthrough_rooms=_optional_int(data, "walkthrough_rooms"),
            kitchen_living_detected=bool(data.get("kitchen_living_detected", False)),
            separate_kitchen_detected=bool(data.get("separate_kitchen_detected", False)),
            layout_class=_optional_str(data, "layout_class"),
            layout_confidence_label=_enum_value(
                LayoutConfidenceLabel,
                data.get("layout_confidence_label"),
                "layout_confidence_label",
            ),
            ss_vs_ai_room_conflict=bool(data.get("ss_vs_ai_room_conflict", False)),
            layout_explanation_user=_required_str(data, "layout_explanation_user"),
            floor_plan_image_ids=_str_tuple(data.get("floor_plan_image_ids", ())),
            building_type_guess=_optional_str(data, "building_type_guess"),
            series_guess=_optional_str(data, "series_guess"),
            wooden_building_risk=bool(data.get("wooden_building_risk", False)),
            stove_heating_risk=bool(data.get("stove_heating_risk", False)),
            mortgage_risk_level=_enum_value(
                MortgageRiskLevel,
                data.get("mortgage_risk_level"),
                "mortgage_risk_level",
            ),
            mortgage_risk_reasons=_str_tuple(data.get("mortgage_risk_reasons", ())),
            mortgage_explanation_user=_required_str(data, "mortgage_explanation_user"),
        )


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AIValidationError(f"Missing required string field: {key}")
    return value.strip()


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AIValidationError(f"Expected string field: {key}")
    value = value.strip()
    return value or None


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AIValidationError(f"Expected integer field: {key}")
    return value


def _required_list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list):
        raise AIValidationError(f"Missing required list field: {key}")
    if not all(isinstance(item, dict) for item in value):
        raise AIValidationError(f"Expected object list field: {key}")
    return value


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise AIValidationError("Expected list of strings")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise AIValidationError("Expected list of strings")
        item = item.strip()
        if item:
            result.append(item)
    return tuple(result)


def _room_groups(value: Any) -> dict[str, tuple[str, ...]]:
    # Accept both a plain {room: [image_ids]} object and the structured-output
    # array form [{"room": ..., "image_ids": [...]}, ...] that Gemini emits,
    # because controlled generation cannot express free-form maps.
    if isinstance(value, list | tuple):
        groups: dict[str, tuple[str, ...]] = {}
        for item in value:
            if not isinstance(item, dict):
                raise AIValidationError("Expected image_groups_by_room entries to be objects")
            room = item.get("room")
            if not isinstance(room, str) or not room.strip():
                raise AIValidationError("Expected image_groups_by_room entry room name")
            groups[room.strip()] = _str_tuple(item.get("image_ids", ()))
        return groups
    if not isinstance(value, dict):
        raise AIValidationError("Expected image_groups_by_room object")
    return {str(key): _str_tuple(group) for key, group in value.items()}


def _enum_value(enum_type, value: Any, field_name: str):
    try:
        return enum_type(value)
    except ValueError as error:
        allowed_values = ", ".join(item.value for item in enum_type)
        raise AIValidationError(
            f"Invalid {field_name}: {value!r}. Expected one of: {allowed_values}"
        ) from error
