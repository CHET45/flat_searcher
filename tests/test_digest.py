from unittest import TestCase

from flat_searcher.shortlist.criteria import parse_criteria
from flat_searcher.shortlist.digest import DigestCounts, render_markdown, select

CRITERIA = parse_criteria(
    """
[price]
max_eur = 60000
bands = [[30000, 40000], [0, 30000]]
[rooms]
order = [2, 1]
[building]
excluded_types = ["Koka"]
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
DAY = "2026-09-24"
PREVIOUS = "2026-09-20"


def _record(
    ss_id: str,
    price: int = 35000,
    rooms: int = 2,
    first_seen: str = "2026-09-01T00:00:00+00:00",
    status: str = "active",
    building_type: str = "Paneļu",
    street: str = "Brīvības",
) -> dict:
    return {
        "ss_id": ss_id,
        "url": f"https://www.ss.com/msg/{ss_id}.html",
        "status": status,
        "first_seen": first_seen,
        "core": {
            "price_eur": price,
            "area_m2": 50,
            "declared_rooms": rooms,
            "floor": 3,
            "total_floors": 5,
            "district": "Teika",
            "street": street,
            "house_number": "1",
            "building_series": "LT proj.",
            "building_type": building_type,
        },
        "fields": {"Sērija": "LT proj."},
        "text": {"title": "", "description": "Gaišs."},
        "market": {"price_per_m2": 700, "district_median_price_per_m2": 1000},
    }


def _build(listings: list[dict], transit: list[dict] | None = None, events: list[dict] | None = None,
           previous: str | None = PREVIOUS) -> tuple[str, DigestCounts]:
    selection = select(
        {record["ss_id"]: record for record in listings}, CRITERIA, transit or [], events or [], previous
    )
    return render_markdown(selection, CRITERIA, DAY), selection.counts


def _row(text: str, ss_id: str) -> str:
    return next(line for line in text.splitlines() if f"/msg/{ss_id}.html" in line)


class DigestTests(TestCase):
    def test_sections_follow_band_then_rooms_preference(self) -> None:
        text, _ = _build(
            [
                _record("cheap-2", price=25000),
                _record("ideal-1", price=35000, rooms=1),
                _record("ideal-2", price=36000),
            ]
        )
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 30k–40k € · 2 rooms", "## 30k–40k € · 1 room", "## <30k € · 2 rooms"])
        self.assertLess(text.index("ideal-2"), text.index("ideal-1"))
        self.assertLess(text.index("ideal-1"), text.index("cheap-2"))

    def test_new_and_cheaper_listings_are_marked_since_the_previous_digest(self) -> None:
        events = [
            {"at": "2026-09-22T10:00:00+00:00", "ss_id": "drop", "event": "listing_changed",
             "changed": {"core.price_eur": [40000, 35000]}},
            {"at": "2026-09-22T10:00:00+00:00", "ss_id": "rise", "event": "listing_changed",
             "changed": {"core.price_eur": [30000, 35000]}},
            {"at": "2026-09-10T10:00:00+00:00", "ss_id": "old-drop", "event": "listing_changed",
             "changed": {"core.price_eur": [40000, 35000]}},
        ]
        text, counts = _build(
            [
                _record("new", first_seen="2026-09-23T08:00:00+00:00"),
                _record("drop"),
                _record("rise"),
                _record("old-drop"),
            ],
            events=events,
        )
        self.assertIn("★", _row(text, "new"))
        self.assertIn("↓", _row(text, "drop"))
        for quiet in ("rise", "old-drop"):
            self.assertNotIn("↓", _row(text, quiet))
            self.assertNotIn("★", _row(text, quiet))
        self.assertEqual((counts.new, counts.price_drops), (1, 1))

    def test_without_a_previous_digest_nothing_is_marked(self) -> None:
        text, counts = _build([_record("new", first_seen="2026-09-23T08:00:00+00:00")], previous=None)
        self.assertNotIn("★", _row(text, "new"))
        self.assertEqual(counts.new, 0)

    def test_counts_cover_rejections_and_removed_candidates(self) -> None:
        events = [
            {"at": "2026-09-21T00:00:00+00:00", "ss_id": "gone", "event": "listing_removed"},
            {"at": "2026-09-21T00:00:00+00:00", "ss_id": "gone-wooden", "event": "listing_removed"},
        ]
        _, counts = _build(
            [
                _record("ok"),
                _record("wooden", building_type="Koka"),
                _record("dear", price=90000),
                _record("gone", status="removed"),
                _record("gone-wooden", status="removed", building_type="Koka"),
            ],
            events=events,
        )
        self.assertEqual((counts.active, counts.candidates, counts.removed), (3, 1, 1))
        self.assertEqual(counts.rejected, {"price": 1, "building_type": 1})

    def test_row_shows_journeys_per_target_approximation_and_escapes_pipes(self) -> None:
        journey = {"minutes": 33, "every_min": 15, "transfers": 1, "walk_m": 350, "legs": [
            {"routes": ["tram 1", "tram 5"], "board": "s1", "alight": "s2", "every_min": 6,
             "shape": None, "span": None},
            {"routes": ["bus 3"], "board": "s3", "alight": "s4", "every_min": 15, "shape": None,
             "span": None},
        ]}
        transit = [
            {"ss_id": "a", "precision": "approx", "targets": {"office": [journey], "campus": []}},
        ]
        text, _ = _build([_record("a", street="Brīvības|x"), _record("b")], transit=transit)
        header = next(line for line in text.splitlines() if line.startswith("| "))
        self.assertIn("office", header)
        self.assertIn("campus", header)
        row = _row(text, "a")
        self.assertIn("tram 1/tram 5 → bus 3 (33 min, every 15)", row)
        self.assertIn("—", row)
        self.assertIn("≈", row)
        self.assertIn("Brīvības\\|x", row)
        self.assertIn("? |", _row(text, "b"))

    def test_every_table_line_has_the_header_column_count(self) -> None:
        text, _ = _build([_record("a"), _record("b", rooms=1)])
        table = [line for line in text.splitlines() if line.startswith("|")]
        widths = {line.replace(r"\|", "").count("|") for line in table}
        self.assertEqual(len(widths), 1, table[:3])

    def test_flags_warn_on_leased_land_implausible_price_and_room_sized_flats(self) -> None:
        owned = _record("owned")
        owned["text"]["description"] = "Zeme ir īpašumā."
        leased = _record("leased")
        leased["text"]["description"] = "Zeme zem mājas ir nomā."
        cheap = _record("cheap")
        cheap["market"] = {"price_per_m2": 390, "district_median_price_per_m2": 1000}
        tiny = _record("tiny")
        tiny["core"]["area_m2"] = 18
        text, _ = _build([owned, leased, cheap, tiny])
        self.assertNotIn("land", _row(text, "owned"))
        self.assertIn("land leased", _row(text, "leased"))
        self.assertIn("price < 0.4× district", _row(text, "cheap"))
        self.assertNotIn("price <", _row(text, "owned"))
        self.assertIn("under 20 m²", _row(text, "tiny"))
        self.assertNotIn("under 20 m²", _row(text, "owned"))

    def test_the_wear_column_shows_the_cadastre_group_and_its_survey_year(self) -> None:
        transit = [
            {"ss_id": "worn", "precision": "exact", "targets": {},
             "building": {"wear": "V4", "wear_date": "2004-05-01", "built": 1962}},
            {"ss_id": "new", "precision": "exact", "targets": {}, "building": {"note": "new build"}},
        ]
        text, _ = _build([_record("worn"), _record("new"), _record("none")], transit=transit)
        self.assertIn("| wear |", next(line for line in text.splitlines() if line.startswith("| ")))
        self.assertIn("| V4 (2004) |", _row(text, "worn"))
        self.assertIn("building wear V4, unsatisfactory: check with the bank", _row(text, "worn"))
        self.assertIn("| new build |", _row(text, "new"))
        self.assertIn("| ? |", _row(text, "none"))
