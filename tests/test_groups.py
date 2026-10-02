from unittest import TestCase

from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.shortlist.groups import group_ads
from flat_searcher.shortlist.rank import evaluate

CRITERIA = parse_criteria("[price]\nmax_eur = 100000\n")


def _ad(ss_id: str, price: int | None = 35000, street: str = "Bauskas", house: str | None = "149",
        floor: int | None = 2, area: float | None = 55.0, rooms: int = 2):
    record = {
        "ss_id": ss_id,
        "status": "active",
        "core": {"price_eur": price, "street": street, "house_number": house, "floor": floor,
                 "area_m2": area, "declared_rooms": rooms},
        "text": {"description": ""},
    }
    return evaluate(record, CRITERIA, None)


def _ids(groups) -> list[list[str]]:
    return [[item.ss_id for item in group] for group in groups]


class GroupAdsTests(TestCase):
    def test_ads_of_one_flat_group_under_the_first_in_order(self) -> None:
        ads = [_ad("a"), _ad("other", house="15"), _ad("b", price=36000), _ad("c", street="bauskas ")]
        self.assertEqual(_ids(group_ads(ads)), [["a", "b", "c"], ["other"]])

    def test_prices_more_than_ten_percent_apart_are_different_flats(self) -> None:
        ads = [_ad("finished", price=51840), _ad("shell", price=30100), _ad("near", price=31000)]
        self.assertEqual(_ids(group_ads(ads)), [["finished"], ["shell", "near"]])

    def test_floor_area_and_rooms_must_all_match(self) -> None:
        ads = [_ad("a"), _ad("floor", floor=3), _ad("area", area=55.5), _ad("rooms", rooms=3)]
        self.assertEqual(_ids(group_ads(ads)), [["a"], ["floor"], ["area"], ["rooms"]])

    def test_ads_without_a_building_floor_area_or_price_stand_alone(self) -> None:
        ads = [_ad("a", house=None), _ad("b", house=None), _ad("c", floor=None), _ad("d", floor=None),
               _ad("e", area=None), _ad("f", area=None), _ad("g", price=None), _ad("h", price=None)]
        self.assertEqual(_ids(group_ads(ads)), [[ad.ss_id] for ad in ads])

    def test_a_house_number_inside_the_street_field_is_enough(self) -> None:
        ads = [_ad("a", street="Kreslera 1 k3", house=""), _ad("b", street="Kreslera 1 k3", house="")]
        self.assertEqual(_ids(group_ads(ads)), [["a", "b"]])
