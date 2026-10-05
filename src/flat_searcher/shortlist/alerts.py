"""One row of alerts per flat: red for what can block a purchase or a mortgage, amber for what needs a check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flat_searcher.shortlist.facts import Fact
from flat_searcher.shortlist.rank import Evaluation

IMPLAUSIBLE_RATIO = 0.4
ROOM_SIZED_M2 = 20
HIGH_MORTGAGE_RISK = ("high", "critical")
WEAR_ALERTS = {
    "V4": "building wear V4, unsatisfactory: check with the bank",
    "V5": "building wear V5, critical",
}
AROUND_WORDS = {
    "industrial": "industrial zone",
    "works": "factory",
    "cemetery": "cemetery",
    "bog": "bog",
    "landfill": "landfill",
    "wastewater": "sewage plant",
    "railway": "railway yard",
}
NUISANCE_ALERT_M = {"industrial": 150, "works": 150, "cemetery": 100, "bog": 500, "landfill": 2000, "wastewater": 1500}
HEATING_ALERTS = {
    "own_boiler": "heating: own boiler",
    "house_boiler": "heating: the house's own boiler room",
}


@dataclass(frozen=True)
class Alert:
    level: Literal["red", "amber"]
    text: str
    quote: str | None = None


def alerts_for(item: Evaluation) -> list[Alert]:
    red: list[Alert] = []
    amber: list[Alert] = []
    if item.stove.value == "heating":
        red.append(_alert("red", "stove heating", item.stove))
    elif item.stove.value == "present":
        amber.append(_alert("amber", "a stove in the flat", item.stove))
    if item.replanning.value == "illegal":
        red.append(_alert("red", "replanning not legalised", item.replanning))
    elif item.replanning.value == "unstated":
        amber.append(_alert("amber", "replanning: check the documents", item.replanning))
    if item.land.value == "leased":
        red.append(_alert("red", "land leased", item.land))
    wear = item.building.get("wear")
    if wear in WEAR_ALERTS:
        red.append(Alert("red", WEAR_ALERTS[wear]))
    judgment = item.record.get("judgment") or {}
    if judgment.get("mortgage_risk") in HIGH_MORTGAGE_RISK:
        reasons = ", ".join(str(reason) for reason in judgment.get("mortgage_reasons") or [])
        said = f" (model: {reasons})" if reasons else " (model)"
        red.append(Alert("red", f"mortgage risk {judgment['mortgage_risk']}{said}"))
    if item.heating.value in HEATING_ALERTS:
        amber.append(_alert("amber", HEATING_ALERTS[item.heating.value], item.heating))
    for kind, near in (item.surroundings.get("around") or {}).items():
        if kind in NUISANCE_ALERT_M and near["m"] <= NUISANCE_ALERT_M[kind]:
            amber.append(Alert("amber", f"{AROUND_WORDS[kind]} {distance_label(near['m'])}"))
    amber.extend(Alert("amber", text) for text in suspicion_flags(item))
    return red + amber


def distance_label(metres: float) -> str:
    return f"{round(metres)} m" if metres < 1000 else f"{metres / 1000:.1f} km"


def suspicion_flags(item: Evaluation) -> list[str]:
    area = (item.record.get("core") or {}).get("area_m2")
    flags = []
    if item.price_ratio is not None and item.price_ratio < IMPLAUSIBLE_RATIO:
        flags.append(f"price < {IMPLAUSIBLE_RATIO}× district")
    if isinstance(area, (int, float)) and area < ROOM_SIZED_M2:
        flags.append(f"under {ROOM_SIZED_M2} m²")
    return flags


def _alert(level: Literal["red", "amber"], text: str, fact: Fact) -> Alert:
    return Alert(level, text, fact.evidence[0] if fact.evidence else None)
