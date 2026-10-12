import io
from unittest import TestCase

from flat_searcher.transit.buildings import parse_buildings

USES = {
    "1110": ("Dzīvojamā māja", "Viena dzīvokļa mājas"),
    "1121": ("Dzīvojamā māja", "Divu dzīvokļu mājas"),
    "1122": ("Dzīvojamā māja", "Triju vai vairāku dzīvokļu mājas"),
    "1274": ("Šķūnis", "Citas, iepriekš neklasificētas, ēkas"),
    "1220": ("Biroju ēka", "Biroju ēkas"),
    "1230": ("Veikals", "Vairumtirdzniecības un mazumtirdzniecības ēkas"),
    "1242": ("Garāža", "Garāžu ēkas"),
    "2112": ("Iela", "Ielas, ceļi un laukumi"),
    "0": ("Ēka", "Nav noteikts"),
}


def _building(
    code: str | None,
    use: str,
    wear: str = "",
    wear_date: str | None = None,
    built: str = "",
    floors: str = "",
    walls: tuple[str, ...] = (),
) -> str:
    name, use_name = USES[use]
    variscode = f"<VARISCode>{code}</VARISCode>" if code else ""
    dated = f"<BuildingDepValDate>{wear_date}</BuildingDepValDate>" if wear_date else ""
    elements = (
        "<ConstructionDataList><BuildingElementMaterialKindList><BuildingElementMaterialKind>"
        "<MaterialKindName>Dzelzsbetona pāļi</MaterialKindName></BuildingElementMaterialKind>"
        "</BuildingElementMaterialKindList><BuildingElementName>Pamati</BuildingElementName>"
        "<BuildingElementExploitYear>1975</BuildingElementExploitYear></ConstructionDataList>"
    )
    if walls:
        materials = "".join(
            f"<BuildingElementMaterialKind><MaterialKindName>{material}</MaterialKindName>"
            "</BuildingElementMaterialKind>"
            for material in walls
        )
        elements += (
            f"<ConstructionDataList><BuildingElementMaterialKindList>{materials}"
            "</BuildingElementMaterialKindList>"
            "<BuildingElementName>Sienas (vertikālā konstrukcija)</BuildingElementName>"
            "<BuildingElementExploitYear>1975</BuildingElementExploitYear></ConstructionDataList>"
        )
    return (
        "<BuildingItemData><BuildingBasicData>"
        f"<BuildingCadastreNr>0100{use}0001</BuildingCadastreNr>{variscode}"
        f"<BuildingName>{name}</BuildingName>"
        f"<BuildingUseKind><BuildingUseKindId>{use}</BuildingUseKindId>"
        f"<BuildingUseKindName>{use_name}</BuildingUseKindName></BuildingUseKind>"
        f"<BuildingArea>3120.5</BuildingArea><BuildingGroundFloors>{floors}</BuildingGroundFloors>"
        "<BuildingUndergroundFloors>1</BuildingUndergroundFloors>"
        "<BuildingMaterialKind></BuildingMaterialKind><BuildingPregCount>60</BuildingPregCount>"
        f"<BuildingExploitYear>{built}</BuildingExploitYear>"
        f"<BuildingDeprecation>{wear}</BuildingDeprecation>{dated}"
        "</BuildingBasicData>"
        "<BuildingTypeData><BuildingKind><BuildingKindId>11220101</BuildingKindId>"
        "<BuildingKindName>Daudzdzīvokļu mājas</BuildingKindName></BuildingKind></BuildingTypeData>"
        f"<BuildingElementData>{elements}</BuildingElementData>"
        "<BuildingHistoricalData></BuildingHistoricalData>"
        "<ObjectRelation><ObjectCadastreNr>01000750713</ObjectCadastreNr>"
        "<ObjectType>PARCEL</ObjectType></ObjectRelation>"
        "</BuildingItemData>"
    )


def _file(*buildings: str) -> bytes:
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<BuildingFullData xmlns="http://ivis.eps.gov.lv/XMLSchemas/100007/CadastreRegistry/v1-0" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<PreparedDate>2026-09-26</PreparedDate><PacketId>5C687023-DB9C</PacketId>"
        "<FilePartCount>1</FilePartCount><FilePartNr>1</FilePartNr><ATVK>0001000</ATVK>"
        f"<BuildingItemList>{''.join(buildings)}</BuildingItemList></BuildingFullData>"
    )
    return text.encode("utf-8")


PANELS = "Saliekamā dzelzsbetona paneļi"
RIGA = _file(
    _building("101", "1122", "V3", "2019-05-14", "1975", "9", (PANELS,)),
    _building("101", "1274", "V5", "2021-03-01", "1980", "1", ("Silikātķieģeļi",)),
    _building("102", "1122", "V2", "2023-11-02", "1912", "5", ("Ķieģeļu mūris", "Koka karkass")),
    _building("102", "1121", "V4", None, "1905", "2", ("Koks",)),
    _building("103", "1122", "", None, "", "", ()),
    _building("104", "1122", "45", "2008-02-20", "1960", "4", ()),
    _building("105", "1110", "V1", "2020-03-09", "2019", "1", ()),
    _building(None, "1122", "V2", None, "1999", "5", ()),
)


class ParseBuildingsTests(TestCase):
    def setUp(self) -> None:
        self.buildings = parse_buildings(io.BytesIO(RIGA))

    def test_an_apartment_building_keeps_its_wear_group_survey_date_age_floors_and_walls(self) -> None:
        self.assertEqual(
            self.buildings["101"],
            {"wear": "V3", "wear_date": "2019-05-14", "built": 1975, "floors": 9, "walls": PANELS,
             "use": "1122"},
        )

    def test_every_address_with_a_building_that_can_hold_a_flat_is_kept(self) -> None:
        self.assertEqual(sorted(self.buildings), ["101", "102", "103", "104", "105"])

    def test_an_address_without_an_apartment_building_takes_the_building_that_stands_there(self) -> None:
        offices = parse_buildings(io.BytesIO(_file(
            _building("8", "1274", "V5", None, "1960", "1"),
            _building("8", "1220", "V2", "2018-06-01", "1974", "12", (PANELS,)),
        )))
        self.assertEqual(
            offices["8"],
            {"wear": "V2", "wear_date": "2018-06-01", "built": 1974, "floors": 12, "walls": PANELS,
             "use": "1220"},
        )
        self.assertEqual(self.buildings["105"]["use"], "1110")
        unregistered = parse_buildings(io.BytesIO(_file(_building("6", "0", "V1", built="2024"))))
        self.assertEqual(unregistered["6"]["use"], "0")
        self.assertEqual(unregistered["6"]["wear"], "V1")

    def test_an_apartment_building_is_preferred_over_a_worse_building_of_another_use(self) -> None:
        apartment, office = _building("9", "1122", "V2"), _building("9", "1220", "V5")
        for order in ((apartment, office), (office, apartment)):
            found = parse_buildings(io.BytesIO(_file(*order)))["9"]
            self.assertEqual((found["use"], found["wear"], "note" in found), ("1122", "V2", False))

    def test_sheds_garages_and_structures_never_stand_in_for_the_building(self) -> None:
        found = parse_buildings(io.BytesIO(_file(
            _building("1", "1274", "V3"), _building("2", "1242", "V3"), _building("3", "2112", "V3"),
            _building("4", "1242", "V5"), _building("4", "1230", "V1"),
        )))
        self.assertEqual(sorted(found), ["4"])
        self.assertEqual((found["4"]["use"], found["4"]["wear"]), ("1230", "V1"))

    def test_several_buildings_of_other_uses_report_the_worst_one(self) -> None:
        found = parse_buildings(io.BytesIO(_file(_building("7", "1230", "V3"), _building("7", "1220", "V4"))))
        self.assertEqual((found["7"]["use"], found["7"]["wear"], found["7"]["note"]), ("1220", "V4", "several buildings"))

    def test_several_apartment_buildings_at_one_address_report_the_worst_one(self) -> None:
        self.assertEqual(
            self.buildings["102"],
            {"wear": "V4", "wear_date": None, "built": 1905, "floors": 2, "walls": "Koks",
             "use": "1121", "note": "several buildings"},
        )

    def test_an_empty_record_has_no_wear_age_floors_or_walls(self) -> None:
        self.assertEqual(
            self.buildings["103"],
            {"wear": None, "wear_date": None, "built": None, "floors": None, "walls": None,
             "use": "1122"},
        )

    def test_an_old_percentage_is_not_a_wear_group(self) -> None:
        self.assertIsNone(self.buildings["104"]["wear"])
        self.assertEqual(self.buildings["104"]["wear_date"], "2008-02-20")

    def test_every_wall_material_is_listed(self) -> None:
        walls = ("Ķieģeļu mūris", "Koka karkass")
        single = parse_buildings(io.BytesIO(_file(_building("7", "1122", "V2", walls=walls))))
        self.assertEqual(single["7"]["walls"], "Ķieģeļu mūris; Koka karkass")

    def test_a_better_group_does_not_hide_a_worse_one_whatever_the_order(self) -> None:
        worse_first = _file(_building("9", "1122", "V5"), _building("9", "1122", "V1"))
        unknown_first = _file(_building("9", "1122"), _building("9", "1122", "V2"))
        self.assertEqual(parse_buildings(io.BytesIO(worse_first))["9"]["wear"], "V5")
        self.assertEqual(parse_buildings(io.BytesIO(unknown_first))["9"]["wear"], "V2")
