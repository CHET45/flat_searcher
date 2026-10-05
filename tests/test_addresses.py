from unittest import TestCase

from flat_searcher.transit.addresses import AddressIndex, RegisterEntry, parse_register_rows

REGISTER = [
    ("Aleksandra Čaka iela 20", 56.9601, 24.1301),
    ("Aleksandra Čaka iela 22", 56.9602, 24.1302),
    ("Jāņa Daliņa iela 8", 56.9701, 24.1201),
    ("Jūrmalas gatve 100", 56.9524, 24.0004),
    ("Stacijas laukums 2", 56.9468, 24.1203),
    ("Vecmīlgrāvja 1. līnija 54", 57.0301, 24.1101),
    ("Čiekurkalna 7. šķērslīnija 7A", 56.9801, 24.1601),
    ("Brīvības iela 200", 56.9701, 24.1501),
    ("Brīvības gatve 201", 56.9736, 24.1600),
    ("Juglas iela 23", 56.9601, 24.2001),
    ("Mazā Juglas iela 23", 56.9501, 24.2101),
    ("Kaivas iela 50 k-3", 56.9301, 24.2201),
    ("Tērbatas iela 59/61", 56.9571, 24.1301),
    ("Dravnieku iela 3 k-3", 56.9201, 24.1701),
    ("Slāvu iela 10", 56.9401, 24.1601),
    ("Slāvu iela 30", 56.9421, 24.1621),
    ("Anniņmuižas bulvāris 26A", 56.9595, 24.0228),
    ("Jēkaba Daliņa iela 2", 56.9001, 24.1001),
]


class ParseRegisterRowsTests(TestCase):
    def test_keeps_existing_riga_buildings_with_wgs84_coordinates_code_and_planned_flag(self) -> None:
        rows = [
            {"KODS": "101", "STATUSS": "EKS", "FOR_BUILD": "N", "STD": "Skolas iela 1, Rīga, LV-1010",
             "DD_N": "56.95", "DD_E": "24.11"},
            {"KODS": "102", "STATUSS": "DEL", "FOR_BUILD": "N", "STD": "Skolas iela 3, Rīga, LV-1010",
             "DD_N": "56.95", "DD_E": "24.11"},
            {"KODS": "103", "STATUSS": "EKS", "FOR_BUILD": "N", "STD": "Skolas iela 1, Jūrmala, LV-2015",
             "DD_N": "56.9", "DD_E": "23.7"},
            {"KODS": "104", "STATUSS": "EKS", "FOR_BUILD": "N", "STD": "\"Riņņi\", Vecates pag., LV-4211",
             "DD_N": "57.7", "DD_E": "25.1"},
            {"KODS": "105", "STATUSS": "EKS", "FOR_BUILD": "N", "STD": "Skolas iela 5, Rīga, LV-1010",
             "DD_N": "", "DD_E": ""},
            {"KODS": "106", "STATUSS": "EKS", "FOR_BUILD": "Y", "STD": "Skolas iela 7, Rīga, LV-1010",
             "DD_N": "56.96", "DD_E": "24.12"},
        ]
        self.assertEqual(
            parse_register_rows(rows),
            [("Skolas iela 1", 56.95, 24.11, "101", False), ("Skolas iela 7", 56.96, 24.12, "106", True)],
        )


class LocateTests(TestCase):
    def setUp(self) -> None:
        self.index = AddressIndex(REGISTER)

    def assertLocated(self, street: str, house: str | None, matched: str, precision: str = "exact") -> None:
        location = self.index.locate(street, house)
        self.assertIsNotNone(location, (street, house))
        assert location is not None
        self.assertEqual((location.matched, location.precision), (matched, precision), (street, house))

    def test_surname_only_street_matches_the_full_register_name(self) -> None:
        self.assertLocated("Čaka", "20", "Aleksandra Čaka iela 20")

    def test_initials_match_first_names(self) -> None:
        self.assertLocated("J. Daliņa", "8", "Jāņa Daliņa iela 8")

    def test_abbreviated_street_types(self) -> None:
        self.assertLocated("Jūrmalas g.", "100", "Jūrmalas gatve 100")
        self.assertLocated("Stacijas l.", "2", "Stacijas laukums 2")
        self.assertLocated("Vecmīlgrāvja 1. l.", "54", "Vecmīlgrāvja 1. līnija 54")
        self.assertLocated("Čiekurkalna 7. šķ l.", "7a", "Čiekurkalna 7. šķērslīnija 7A")
        self.assertLocated("Anniņmuižas b.", "26a", "Anniņmuižas bulvāris 26A")

    def test_defaulted_type_falls_back_to_the_street_that_has_the_house(self) -> None:
        self.assertLocated("Brīvības", "201", "Brīvības gatve 201")
        self.assertLocated("Brīvības", "200", "Brīvības iela 200")

    def test_exact_name_beats_a_longer_name_with_the_same_tail(self) -> None:
        self.assertLocated("Juglas", "23", "Juglas iela 23")
        self.assertLocated("Mazā Juglas", "23", "Mazā Juglas iela 23")

    def test_building_block_spellings(self) -> None:
        self.assertLocated("Kaivas", "50k3", "Kaivas iela 50 k-3")
        self.assertLocated("Kaivas", "50 к3", "Kaivas iela 50 k-3")
        self.assertLocated("Kaivas", "50", "Kaivas iela 50 k-3")

    def test_house_number_inside_the_street_field(self) -> None:
        self.assertLocated("Dravnieku 3 k-3", None, "Dravnieku iela 3 k-3")

    def test_block_letter_left_in_the_street_field(self) -> None:
        self.assertLocated("Kaivas 50 k", "3", "Kaivas iela 50 k-3")
        self.assertLocated("Dravnieku 3k - 3,", None, "Dravnieku iela 3 k-3")

    def test_double_numbers_match_either_way(self) -> None:
        self.assertLocated("Tērbatas", "59/61", "Tērbatas iela 59/61")
        self.assertLocated("Tērbatas", "59", "Tērbatas iela 59/61")

    def test_unknown_house_takes_the_nearest_number_on_the_street(self) -> None:
        self.assertLocated("Slāvu", "18", "Slāvu iela 10", "approx")
        self.assertLocated("Slāvu", "26", "Slāvu iela 30", "approx")

    def test_missing_house_number_is_approximate(self) -> None:
        location = self.index.locate("Slāvu", None)
        assert location is not None
        self.assertEqual(location.precision, "approx")

    def test_unknown_street_is_not_located(self) -> None:
        self.assertIsNone(self.index.locate("Neesošā", "1"))
        self.assertIsNone(self.index.locate("", "1"))

    def test_two_equally_good_streets_are_ambiguous(self) -> None:
        self.assertIsNone(self.index.locate("J. Daliņa", "5"))

    def test_the_located_building_carries_its_register_code_and_planned_flag(self) -> None:
        index = AddressIndex(
            [
                RegisterEntry("Skolas iela 1", 56.95, 24.11, "101", False),
                RegisterEntry("Skolas iela 7", 56.96, 24.12, "106", True),
            ]
        )
        exact = index.locate("Skolas", "7")
        approx = index.locate("Skolas", "2")
        assert exact is not None and approx is not None
        self.assertEqual((exact.precision, exact.address_code, exact.planned), ("exact", "106", True))
        self.assertEqual((approx.precision, approx.address_code, approx.planned), ("approx", "101", False))

    def test_an_entry_without_a_code_locates_as_before(self) -> None:
        location = self.index.locate("Čaka", "20")
        assert location is not None
        self.assertEqual((location.address_code, location.planned), (None, False))

    def test_free_text_address_of_a_target(self) -> None:
        location = self.index.locate_text("Anniņmuižas bulvāris 26a, Rīga")
        assert location is not None
        self.assertEqual(location.matched, "Anniņmuižas bulvāris 26A")
        self.assertAlmostEqual(location.lat, 56.9595)
        self.assertIsNone(self.index.locate_text("Nowhere"))
