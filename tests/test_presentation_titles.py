from unittest import TestCase

from flat_searcher.presentation import format_apartment_title, format_ai_room_label


class PresentationTitleTests(TestCase):
    def test_formats_apartment_title_with_ai_and_ss_rooms(self) -> None:
        title = format_apartment_title(
            district="Teika",
            street="Brivibas gatve",
            effective_private_rooms=2,
            declared_rooms_ss=3,
            area_m2=58,
            price_eur=112_000,
        )

        self.assertEqual(
            title,
            "Teika - Brivibas gatve - AI: 2 private / SS: 3 - 58 m2 - 112 000 EUR",
        )

    def test_formats_kitchen_living_and_unclear_labels(self) -> None:
        self.assertEqual(format_ai_room_label(1, kitchen_living_detected=True), "1 private + kitchen-living")
        self.assertEqual(format_ai_room_label(None), "unclear")
