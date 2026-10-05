from unittest import TestCase

from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.shortlist.rank import evaluate
from flat_searcher.shortlist.alerts import Alert, alerts_for

CRITERIA = parse_criteria("[price]\nmax_eur = 100000\n")


def _alerts(description: str = "", area: float = 50, ratio: float = 1.0, judgment: dict | None = None,
            series: str = "P. kara", building: dict | None = None, around: dict | None = None) -> list[Alert]:
    record = {
        "ss_id": "x",
        "status": "active",
        "core": {"price_eur": 40000, "area_m2": area, "declared_rooms": 2, "building_series": series},
        "fields": {"Sērija": series},
        "text": {"title": "", "description": description},
        "market": {"price_per_m2": 1000 * ratio, "district_median_price_per_m2": 1000},
        "judgment": judgment or {},
    }
    transit = {"precision": "exact", "targets": {}, "building": building or {},
               "surroundings": {"around": around or {}}} if building or around else None
    return alerts_for(evaluate(record, CRITERIA, transit))


def _texts(alerts: list[Alert]) -> list[tuple[str, str]]:
    return [(alert.level, alert.text) for alert in alerts]


class AlertTests(TestCase):
    def test_a_plain_flat_has_no_alerts(self) -> None:
        self.assertEqual(_alerts("Mājā ir centrālā apkure. Zeme ir īpašumā."), [])

    def test_stove_heating_is_red_and_quotes_the_listing(self) -> None:
        alerts = _alerts("Centrālā apkure. Istabā arī krāsns apkure.")
        self.assertEqual(_texts(alerts), [("red", "stove heating")])
        self.assertEqual(alerts[0].quote, "istabā arī krāsns apkure.")

    def test_a_stove_without_stove_heating_is_amber(self) -> None:
        self.assertEqual(_texts(_alerts("Saglabājusies podiņu krāsns.")), [("amber", "a stove in the flat")])

    def test_replanning_not_legalised_is_red_and_one_without_a_status_is_amber(self) -> None:
        self.assertEqual(_texts(_alerts("Pārplānojums nav saskaņots.")), [("red", "replanning not legalised")])
        self.assertEqual(
            _texts(_alerts("Dzīvoklis ir pārplānots.")), [("amber", "replanning: check the documents")]
        )
        self.assertEqual(_alerts("Pārplānojums ir saskaņots."), [])

    def test_leased_land_and_a_high_mortgage_risk_are_red(self) -> None:
        self.assertEqual(_texts(_alerts("Zeme zem mājas ir nomā.")), [("red", "land leased")])
        alerts = _alerts(judgment={"mortgage_risk": "critical", "mortgage_reasons": ["share sale", "no plan"]})
        self.assertEqual(_texts(alerts), [("red", "mortgage risk critical (model: share sale, no plan)")])
        self.assertIsNone(alerts[0].quote)
        self.assertEqual(_alerts(judgment={"mortgage_risk": "medium", "mortgage_reasons": []}), [])

    def test_a_heating_source_other_than_the_city_is_amber(self) -> None:
        self.assertEqual(_texts(_alerts("Autonoma gāzes apkure.")), [("amber", "heating: own boiler")])
        self.assertEqual(
            _texts(_alerts("Mājā ir centrālā gāzes apkure.")), [("amber", "heating: the house's own boiler room")]
        )

    def test_an_implausible_price_and_a_room_sized_flat_are_amber(self) -> None:
        self.assertEqual(_texts(_alerts(ratio=0.3)), [("amber", "price < 0.4× district")])
        self.assertEqual(_texts(_alerts(area=18)), [("amber", "under 20 m²")])

    def test_unsatisfactory_and_critical_building_wear_are_red(self) -> None:
        self.assertEqual(
            _texts(_alerts(building={"wear": "V4", "wear_date": "2019-03-02"})),
            [("red", "building wear V4, unsatisfactory: check with the bank")],
        )
        self.assertEqual(_texts(_alerts(building={"wear": "V5"})), [("red", "building wear V5, critical")])
        self.assertEqual(_alerts(building={"wear": "V3"}), [])
        self.assertEqual(_alerts(building={"note": "new build"}), [])

    def test_a_close_nuisance_is_amber_and_a_farther_one_is_not(self) -> None:
        around = {"industrial": {"name": "", "m": 120}, "cemetery": {"name": "Meža kapi", "m": 400},
                  "landfill": {"name": "Getliņi", "m": 1800}, "bog": {"name": "", "m": 900}}
        self.assertEqual(_texts(_alerts(around=around)),
                         [("amber", "industrial zone 120 m"), ("amber", "landfill 1.8 km")])
        self.assertEqual(_alerts(around={"industrial": {"name": "", "m": 151}}), [])

    def test_red_comes_before_amber(self) -> None:
        alerts = _alerts("Autonoma gāzes apkure. Zeme zem mājas ir nomā.", area=18)
        self.assertEqual([alert.level for alert in alerts], ["red", "amber", "amber"])
