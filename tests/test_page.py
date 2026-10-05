import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless

from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.shortlist.digest import select
from flat_searcher.shortlist.page import MAP_GLOBAL, PageFiles, render_page

CRITERIA = parse_criteria(
    """
[price]
max_eur = 60000
bands = [[30000, 40000], [0, 30000]]
[rooms]
order = [2, 1]
[today]
size = 2
[[transit.targets]]
name = "office"
lat = 56.9
lon = 24.1
[[transit.targets]]
name = "campus"
lat = 56.95
lon = 24.0
"""
)


def _record(ss_id: str, price: int = 35000, rooms: int = 2, description: str = "Gaišs.",
            first_seen: str = "2026-09-01T00:00:00+00:00", house: str = "1") -> dict:
    return {
        "ss_id": ss_id,
        "url": f"https://www.ss.com/msg/{ss_id}.html",
        "status": "active",
        "first_seen": first_seen,
        "core": {
            "price_eur": price, "area_m2": 50, "declared_rooms": rooms, "floor": 3,
            "total_floors": 5, "district": "Teika", "street": "Brīvības", "house_number": house,
            "building_series": "P. kara", "building_type": "Mūra",
        },
        "fields": {"Sērija": "P. kara"},
        "text": {"title": "", "description": description},
        "market": {"price_per_m2": 700, "district_median_price_per_m2": 1000},
    }


WALKS = [{"m": 200, "line": "_p~iF~ps|U"}, {"m": 300, "line": "_p~iF~ps|U"}]
JOURNEY = {"minutes": 24, "every_min": 10, "transfers": 0, "walk_m": 500, "walks": WALKS, "legs": [
    {"routes": ["bus 24"], "board": "s1", "alight": "s2", "every_min": 10, "shape": "shape_24",
     "span": [2, 9]},
]}
SLOW = {**JOURNEY, "minutes": 40, "every_min": 4}
RARE = {**JOURNEY, "minutes": 22, "every_min": 30}
TRANSIT_MAP = {"shapes": {"shape_24": "_p~iF~ps|U"},
               "stops": {"s1": [56.9, 24.1, "A"], "s2": [56.91, 24.1, "B"], "s9": [56.8, 24.0, "Z"]},
               "streets": {"roads": [["_p~iF~ps|U"], [], []], "rail": [], "water": []}}
TARGETS = {"office": {"lat": 56.9, "lon": 24.1}, "campus": {"lat": 56.95, "lon": 24.0}}


def _located(ss_id: str, office: list | None = None, campus: list | None = None) -> dict:
    return {"ss_id": ss_id, "precision": "exact", "lat": 56.91, "lon": 24.11,
            "targets": {"office": [JOURNEY] if office is None else office,
                        "campus": [JOURNEY] if campus is None else campus},
            "day": "2026-09-29", "window": "07:00-10:00"}


def _page(listings: list[dict], transit: list[dict] | None = None, photos: set[str] | None = None,
          events: list[dict] | None = None) -> PageFiles:
    selection = select(
        {record["ss_id"]: record for record in listings},
        CRITERIA,
        transit or [],
        events or [],
        "2026-09-20",
    )
    return render_page(selection, CRITERIA, "2026-09-24", photos or set(), TARGETS, TRANSIT_MAP)


def _data(page: PageFiles) -> dict:
    match = re.search(r'<script id="data" type="application/json">(.*?)</script>', page.html, re.S)
    assert match is not None
    return json.loads(match.group(1))


def _map(page: PageFiles) -> dict:
    prefix = f"window.{MAP_GLOBAL} = "
    assert page.map_script.startswith(prefix)
    return json.loads(page.map_script[len(prefix):].rstrip().removesuffix(";"))


def _cards(page: PageFiles) -> dict[str, dict]:
    return {card["id"]: card for card in _data(page)["cards"]}


class RenderPageTests(TestCase):
    def test_page_follows_the_artifact_contract_head(self) -> None:
        html = _page([_record("a")]).html
        self.assertTrue(html.startswith('<meta charset="utf-8">'))
        self.assertIn("<title>", html[:8192])
        self.assertIn('<meta name="robots" content="noindex">', html[:8192])
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html[:8192])
        self.assertNotIn("<html", html)
        self.assertNotIn("<body", html)

    def test_cards_keep_digest_order_and_carry_what_the_card_shows(self) -> None:
        listings = [
            _record("cheap", price=25000, house="3"),
            _record("ideal-1", rooms=1, house="2"),
            _record("ideal-2", description="2 izolētas istabas. Zeme ir īpašumā.",
                    first_seen="2026-09-23T00:00:00+00:00"),
        ]
        listings[2]["fields"]["Uzņēmums"] = "SIA Aģentūra"
        listings[2]["judgment"] = {"building_condition": "renovated", "mortgage_risk": "low",
                                   "mortgage_reasons": []}
        page = _page(listings, [{**_located("ideal-2", campus=[]), "precision": "approx"}], {"ideal-2"})
        data = _data(page)

        self.assertEqual([card["id"] for card in data["cards"]], ["ideal-2", "ideal-1", "cheap"])
        first = data["cards"][0]
        self.assertTrue(first["new"])
        self.assertFalse(first["cheaper"])
        self.assertEqual(first["firstSeen"], "2026-09-23")
        self.assertEqual(first["best"]["campus"], None)
        self.assertEqual(first["best"]["office"], {
            "minutes": 24, "every_min": 10, "transfers": 0, "walk_m": 500,
            "legs": [{"routes": ["bus 24"], "board": "s1", "alight": "s2", "every_min": 10}]})
        self.assertEqual(first["reached"], 1)
        self.assertEqual(data["cards"][1]["best"], {})
        self.assertEqual((first["lat"], first["lon"], first["approx"]), (56.91, 24.11, True))
        self.assertEqual(first["layout"], {"value": "isolated", "source": "text",
                                           "quote": "2 izolētas istabas."})
        self.assertEqual(first["land"], {"value": "owned", "quote": "zeme ir īpašumā."})
        self.assertEqual(first["company"], "SIA Aģentūra")
        self.assertIsNone(data["cards"][1]["company"])
        self.assertEqual(first["model"], {"condition": "renovated", "mortgage": "low", "reasons": []})
        self.assertIsNone(data["cards"][1]["model"])
        self.assertEqual(first["photo"], "photos/ideal-2.jpg")
        self.assertIsNone(data["cards"][1]["photo"])
        self.assertEqual(page.photo_ids, ("ideal-2",))
        self.assertEqual(data["stops"], {"s1": [56.9, 24.1, "A"], "s2": [56.91, 24.1, "B"]})
        self.assertEqual(data["transit"], {"day": "2026-09-29", "window": "07:00-10:00"})
        self.assertEqual(data["targets"], [{"name": "office", "lat": 56.9, "lon": 24.1},
                                           {"name": "campus", "lat": 56.95, "lon": 24.0}])
        self.assertEqual(data["mapScript"], "map.js?v=2026-09-24")
        self.assertEqual((data["counts"]["candidates"], data["counts"]["flats"]), (3, 3))

    def test_the_card_carries_alerts_heating_hot_water_replanning_and_the_sites_own_values(self) -> None:
        record = _record("a", description="Autonoma gāzes apkure. Karstais ūdens no boilera. "
                                          "Dzīvoklis ir pārplānots. Zeme zem mājas ir nomā.")
        record["fields"].update({"Cena": "35 000 € (700 €/m²)", "Platība": "50 m²", "Stāvs": "3/5",
                                 "Istabas": "2", "Tālrunis": "(+371)29-***"})
        card = _cards(_page([record]))["a"]
        self.assertEqual(card["alerts"], [
            {"level": "red", "text": "land leased", "quote": "zeme zem mājas ir nomā."},
            {"level": "amber", "text": "replanning: check the documents", "quote": "dzīvoklis ir pārplānots."},
            {"level": "amber", "text": "heating: own boiler", "quote": "autonoma gāzes apkure."},
        ])
        self.assertEqual(card["heating"], {"value": "own_boiler", "quote": "autonoma gāzes apkure."})
        self.assertEqual(card["hotWater"], {"value": "boiler", "quote": "karstais ūdens no boilera."})
        self.assertEqual(card["replanning"], {"value": "unstated", "quote": "dzīvoklis ir pārplānots."})
        self.assertEqual(card["source"], {"Cena": "35 000 € (700 €/m²)", "Platība": "50 m²", "Istabas": "2",
                                          "Stāvs": "3/5", "Sērija": "P. kara"})
        self.assertNotIn("flags", card)
        self.assertEqual(card["building"], {})
        built = _cards(_page([_record("b")], [{**_located("b"), "building": {"wear": "V3", "wear_date": "2019-03-02", "built": 1975, "floors": 9}}]))["b"]
        self.assertEqual(built["building"], {"wear": "V3", "wear_date": "2019-03-02", "built": 1975, "floors": 9})

    def test_the_card_shows_the_option_with_the_shortest_expected_time(self) -> None:
        page = _page([_record("a")], [_located("a", office=[RARE, SLOW, JOURNEY])])
        self.assertEqual(_cards(page)["a"]["best"]["office"]["minutes"], 24)

    def test_every_option_with_its_walks_lives_in_the_map_script_not_the_page(self) -> None:
        page = _page([_record("a"), _record("b", house="2")], [_located("a", office=[JOURNEY, SLOW])])
        data = _map(page)
        self.assertEqual(data["journeys"], {"a": {"office": [JOURNEY, SLOW], "campus": [JOURNEY]}})
        self.assertEqual(data["shapes"], TRANSIT_MAP["shapes"])
        self.assertEqual(data["streets"], TRANSIT_MAP["streets"])
        self.assertEqual(data["stops"], TRANSIT_MAP["stops"])
        embedded = json.dumps(_data(page))
        self.assertNotIn('"walks"', embedded)
        self.assertNotIn('"streets"', embedded)
        self.assertNotIn('"shapes"', embedded)

    def test_ads_of_one_flat_share_a_card(self) -> None:
        listings = [_record("a", price=35000), _record("b", price=36000), _record("c", price=34900,
                    first_seen="2026-09-23T00:00:00+00:00")]
        events = [{"at": "2026-09-22T10:00:00+00:00", "ss_id": "b", "event": "listing_changed",
                   "changed": {"core.price_eur": [39000, 36000]}}]
        page = _page(listings, photos={"b"}, events=events)
        cards = _data(page)["cards"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["id"], "c")
        self.assertEqual(card["ads"], [{"id": "a", "url": "https://www.ss.com/msg/a.html", "price": 35000},
                                       {"id": "b", "url": "https://www.ss.com/msg/b.html", "price": 36000}])
        self.assertFalse(card["new"])
        self.assertTrue(card["cheaper"])
        self.assertEqual(card["firstSeen"], "2026-09-01")
        self.assertEqual(card["photo"], "photos/b.jpg")
        self.assertEqual(page.photo_ids, ("b",))
        self.assertEqual(_data(page)["counts"]["flats"], 1)

    def test_price_history_lists_the_first_price_and_each_change(self) -> None:
        events = [
            {"at": "2026-09-15T10:00:00+00:00", "ss_id": "a", "event": "listing_changed",
             "changed": {"core.price_eur": [42000, 39000]}},
            {"at": "2026-09-22T10:00:00+00:00", "ss_id": "a", "event": "listing_changed",
             "changed": {"core.price_eur": [39000, 35000]}},
            {"at": "2026-09-22T10:00:00+00:00", "ss_id": "a", "event": "listing_changed",
             "changed": {"core.area_m2": [49, 50]}},
        ]
        card = _cards(_page([_record("a")], events=events))["a"]
        self.assertEqual(card["history"], [["", 42000], ["2026-09-15", 39000], ["2026-09-22", 35000]])
        self.assertEqual(_cards(_page([_record("b")]))["b"]["history"], [])

    def test_today_marks_the_front_and_names_its_rules(self) -> None:
        listings = [_record("big", price=38000, house="1"), _record("small", price=30000, house="2"),
                    _record("dear", price=39000, house="3"), _record("nowhere", house="4")]
        listings[0]["core"]["area_m2"] = 60
        transit = [_located("big"), _located("small"), _located("dear")]
        data = _data(_page(listings, transit))
        cards = {card["id"]: card for card in data["cards"]}
        self.assertEqual({ss_id: card["today"] for ss_id, card in cards.items()},
                         {"big": 1, "small": 1, "dear": None, "nowhere": None})
        self.assertNotIn("not on the ground floor", data["today"]["rules"])
        self.assertEqual(data["today"]["size"], 2)

    def test_seller_text_cannot_close_the_data_script(self) -> None:
        page = _page([_record("x", description="Istabas izolētas </script><script>alert(1)</script>.")])
        self.assertEqual(page.html.count("</script>"), page.html.count("<script"))
        self.assertIn("</script>", _data(page)["cards"][0]["layout"]["quote"])

    def test_sort_keys_take_the_option_best_on_each_metric_and_combine_targets(self) -> None:
        fast = {**JOURNEY, "minutes": 30, "every_min": 15, "transfers": 1, "walk_m": 900}
        slow_direct = {**JOURNEY, "minutes": 40, "every_min": 5, "transfers": 0, "walk_m": 300}
        campus = {**JOURNEY, "minutes": 20, "every_min": 12, "transfers": 0, "walk_m": 400}
        transit = [_located("both", office=[fast, slow_direct], campus=[campus]),
                   _located("one", office=[fast], campus=[])]
        page = _page([_record("both"), _record("one", house="2"), _record("nowhere", house="3")], transit)
        cards = {card_id: card["sort"] for card_id, card in _cards(page).items()}

        self.assertEqual(cards["both"]["time"], {"office": [37.5, 30], "campus": [26, 20], "*": [63.5, 50]})
        self.assertEqual(cards["both"]["transfers"], {"office": [0, 40], "campus": [0, 20], "*": [0, 60]})
        self.assertEqual(cards["both"]["every"], {"office": [5, 40], "campus": [12, 20], "*": [12, 60]})
        self.assertEqual(cards["both"]["walk"], {"office": [300, 40], "campus": [400, 20], "*": [700, 60]})
        self.assertEqual(cards["one"]["time"], {"office": [37.5, 30], "campus": None, "*": None})
        self.assertEqual(cards["nowhere"], {})


NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("page_harness.js")


@skipUnless(NODE, "node runs the page script")
class PageScriptTests(TestCase):
    """The page's own script, run offline: Leaflet and map.js never load."""

    def _run(self, page: PageFiles, actions: list | None = None, storage: dict | None = None,
             now: str = "2026-09-24T12:00:00") -> dict:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.html"
            path.write_bytes(page.html.encode("utf-8"))
            result = subprocess.run(
                [str(NODE), str(HARNESS), str(path), json.dumps(actions or []), json.dumps(storage or {}), now],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _flats(self) -> PageFiles:
        listings = [_record("big", price=38000, house="1"), _record("small", price=30000, house="2"),
                    _record("dear", price=39000, house="3", first_seen="2026-09-23T00:00:00+00:00"),
                    _record("dear-copy", price=39500, house="3")]
        listings[0]["core"]["area_m2"] = 60
        transit = [_located("big"), _located("small"), _located("dear"), _located("dear-copy")]
        return _page(listings, transit)

    def test_the_list_renders_when_the_map_library_cannot_load(self) -> None:
        shown = self._run(self._flats(), [{"click": "tab-map"}])
        self.assertEqual(shown["cards"], ["small", "big"])
        self.assertIn("could not load", shown["mapNote"])
        self.assertIn("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js", shown["loaded"])
        self.assertIn("map.js?v=2026-09-24", shown["loaded"])
        opened_on_map = self._run(self._flats(), storage={"riga-flat-shortlist-filters": '{"tab": "map"}'})
        self.assertEqual(opened_on_map["cards"], ["small", "big"])

    def test_today_first_then_every_flat_one_card_per_flat(self) -> None:
        shown = self._run(self._flats(), [{"click": "view-all"}])
        self.assertEqual((shown["today"], shown["all"]), ("Today 2", "All 3"))
        self.assertEqual(shown["cards"], ["small", "big", "dear"])
        self.assertIn("The same flat in 2 ads, 39 000–39 500 €", shown["list"])

    def test_a_hidden_flat_leaves_every_view_and_stays_hidden_for_all_its_ads(self) -> None:
        shown = self._run(self._flats(), [{"click": "view-all"}, {"click": "list", "act": "hide", "id": "dear"}])
        self.assertEqual(shown["cards"], ["small", "big"])
        self.assertEqual(json.loads(shown["storage"]["riga-flat-shortlist-marks"]),
                         {"dear": "hide", "dear-copy": "hide"})
        again = self._run(self._flats(), [{"change": "f-marks", "value": "hidden"}],
                          {"riga-flat-shortlist-marks": shown["storage"]["riga-flat-shortlist-marks"],
                           "riga-flat-shortlist-filters": '{"view": "all"}'})
        self.assertEqual(again["cards"], ["dear"])

    def test_new_since_the_last_visit_uses_the_day_of_the_previous_visit(self) -> None:
        first = self._run(self._flats())
        self.assertTrue(first["visitDisabled"])
        self.assertEqual(json.loads(first["storage"]["riga-flat-shortlist-visits"]),
                         {"current": "2026-09-24", "previous": None})
        shown = self._run(self._flats(), [{"click": "view-all"}, {"change": "f-visit", "checked": True}],
                          {"riga-flat-shortlist-visits": '{"current": "2026-09-22"}'})
        self.assertFalse(shown["visitDisabled"])
        self.assertEqual(shown["cards"], [])
        later = self._run(self._flats(), [{"click": "view-all"}, {"change": "f-visit", "checked": True}],
                          {"riga-flat-shortlist-visits": '{"current": "2026-08-30"}'})
        self.assertEqual(later["cards"], ["small", "big", "dear"])

    def test_a_page_older_than_a_day_says_how_old_its_data_is(self) -> None:
        self.assertIsNone(self._run(self._flats(), now="2026-09-24T23:30:00")["stale"])
        self.assertEqual(self._run(self._flats(), now="2026-09-27T08:00:00")["stale"],
                         "Data from 24.09, 3 days old: prices and availability may have changed.")

    def test_alerts_open_the_card_and_the_heating_line_follows_the_pills(self) -> None:
        listings = [_record("stove", description="Centrālā apkure. Istabā arī krāsns apkure."),
                    _record("plain", house="2", description="Mājā ir centrālā apkure.")]
        building = {"wear": "V4", "wear_date": "2019-03-02", "built": 1975, "floors": 9}
        transit = [{**_located("stove"), "building": building}, _located("plain")]
        listed = self._run(_page(listings, transit), [{"click": "view-all"}])["list"]
        cards = {}
        for part in listed.split("<article")[1:]:
            found = re.search(r'id="c-([^"]+)"', part)
            assert found is not None
            cards[found.group(1)] = part
        self.assertIn('<span class="alert red">⚠ stove heating</span>', cards["stove"])
        self.assertLess(cards["stove"].index('class="alerts"'), cards["stove"].index('class="facts"'))
        self.assertIn("Heating: <b>city, central</b> · Hot water: <b>not stated</b>", cards["stove"])
        self.assertIn("“istabā arī krāsns apkure.”", cards["stove"])
        self.assertIn("⚠ building wear V4, unsatisfactory: check with the bank", cards["stove"])
        self.assertIn("Building: built 1975 · 9 floors · wear <b>V4 unsatisfactory</b> (VZD 2019)", cards["stove"])
        self.assertNotIn('class="alerts"', cards["plain"])
        self.assertNotIn("Building:", cards["plain"])

    def test_turning_the_phone_keeps_the_card_that_was_at_the_top_in_place(self) -> None:
        portrait = {"c-small": {"top": -400, "bottom": -100}, "c-big": {"top": -100, "bottom": 200},
                    "c-dear": {"top": 200, "bottom": 500}}
        landscape = {"c-small": {"top": -900, "bottom": -500}, "c-big": {"top": -500, "bottom": -150},
                     "c-dear": {"top": -150, "bottom": 200}}
        turn = [{"click": "view-all"}, {"layout": portrait}, {"event": "scroll"}, {"wait": 20},
                {"event": "orientationchange"}, {"layout": landscape}, {"event": "resize"}, {"wait": 300}]
        self.assertEqual(self._run(self._flats(), turn)["scrolled"], [[0, -400]])
        resize_only = turn[:4] + [{"layout": landscape}, {"event": "resize"}, {"wait": 300}]
        self.assertEqual(self._run(self._flats(), resize_only)["scrolled"], [[0, -400]])
        self.assertEqual(self._run(self._flats(), turn[:2] + turn[4:])["scrolled"], [])

    def test_sorting_by_price_reorders_the_cards(self) -> None:
        shown = self._run(self._flats(), [{"click": "view-all"}, {"change": "f-sort", "value": "area"}])
        self.assertEqual(shown["cards"], ["big", "small", "dear"])
