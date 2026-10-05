"""Wear group, age, floors and walls of Riga's apartment buildings from the VZD cadastre."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from typing import IO, Any

NAMESPACE = "{http://ivis.eps.gov.lv/XMLSchemas/100007/CadastreRegistry/v1-0}"
APARTMENT_USE = "112"
WEAR_GROUPS = ("V1", "V2", "V3", "V4", "V5")
WALLS_ELEMENT = "Sienas"
SEVERAL = "several buildings"

Building = dict[str, Any]


def _path(*names: str) -> str:
    return "/".join(NAMESPACE + name for name in names)


_ITEM = _path("BuildingItemData")
_BASIC = _path("BuildingBasicData")
_CODE = _path("VARISCode")
_USE = _path("BuildingUseKind", "BuildingUseKindId")
_WEAR = _path("BuildingDeprecation")
_WEAR_DATE = _path("BuildingDepValDate")
_BUILT = _path("BuildingExploitYear")
_FLOORS = _path("BuildingGroundFloors")
_ELEMENTS = _path("BuildingElementData", "ConstructionDataList")
_ELEMENT_NAME = _path("BuildingElementName")
_MATERIALS = _path(
    "BuildingElementMaterialKindList", "BuildingElementMaterialKind", "MaterialKindName"
)


def parse_buildings(stream: IO[bytes]) -> dict[str, Building]:
    found: dict[str, list[Building]] = defaultdict(list)
    for _, element in ET.iterparse(stream):
        if element.tag != _ITEM:
            continue
        basic = element.find(_BASIC)
        if basic is not None:
            code = _text(basic, _CODE)
            use = _text(basic, _USE)
            if code and use.startswith(APARTMENT_USE):
                found[code].append(_building(element, basic, use))
        element.clear()
    return {code: _worst(buildings) for code, buildings in found.items()}


def _building(element: ET.Element, basic: ET.Element, use: str) -> Building:
    wear = _text(basic, _WEAR)
    return {
        "wear": wear if wear in WEAR_GROUPS else None,
        "wear_date": _date(_text(basic, _WEAR_DATE)),
        "built": _integer(_text(basic, _BUILT)),
        "floors": _integer(_text(basic, _FLOORS)),
        "walls": _walls(element),
        "use": use,
    }


def _worst(buildings: list[Building]) -> Building:
    if len(buildings) == 1:
        return buildings[0]
    worst = max(buildings, key=lambda building: _rank(building["wear"]))
    return {**worst, "note": SEVERAL}


def _rank(wear: str | None) -> int:
    return WEAR_GROUPS.index(wear) if wear else -1


def _walls(element: ET.Element) -> str | None:
    for construction in element.iterfind(_ELEMENTS):
        if _text(construction, _ELEMENT_NAME).startswith(WALLS_ELEMENT):
            materials = ((node.text or "").strip() for node in construction.iterfind(_MATERIALS))
            return "; ".join(material for material in materials if material) or None
    return None


def _text(element: ET.Element, path: str) -> str:
    return (element.findtext(path) or "").strip()


def _integer(text: str) -> int | None:
    return int(text) if text.isdecimal() else None


def _date(text: str) -> str | None:
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None
