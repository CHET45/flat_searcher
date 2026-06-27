from unittest import TestCase

from flat_searcher.geo import Coordinate, OverpassPOIProvider, POICategory


class FakeOverpassTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.endpoint: str | None = None
        self.query: str | None = None

    def post_json(self, endpoint: str, query: str) -> dict[str, object]:
        self.endpoint = endpoint
        self.query = query
        return self.payload


class OverpassPOIProviderTests(TestCase):
    def test_fetch_nearby_parses_nodes_and_way_centers(self) -> None:
        transport = FakeOverpassTransport(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 10,
                        "lat": 56.95,
                        "lon": 24.1,
                        "tags": {
                            "shop": "supermarket",
                            "name": "Example Market",
                        },
                    },
                    {
                        "type": "way",
                        "id": 20,
                        "center": {"lat": 56.951, "lon": 24.102},
                        "tags": {
                            "public_transport": "platform",
                            "name": "Example Stop",
                        },
                    },
                    {
                        "type": "node",
                        "id": 30,
                        "lat": 56.952,
                        "lon": 24.103,
                        "tags": {"amenity": "cafe"},
                    },
                ]
            }
        )
        provider = OverpassPOIProvider(
            "https://overpass.test/api",
            transport=transport,
        )

        pois = provider.fetch_nearby(Coordinate(56.95, 24.1), 1_800)

        self.assertEqual(len(pois), 2)
        self.assertEqual(pois[0].category, POICategory.GROCERY_SHOP)
        self.assertEqual(pois[0].name, "Example Market")
        self.assertEqual(pois[1].category, POICategory.TRANSPORT_STOP)
        self.assertEqual(pois[1].coordinate, Coordinate(56.951, 24.102))
        self.assertEqual(transport.endpoint, "https://overpass.test/api")
        self.assertIn("around:1800,56.9500000,24.1000000", transport.query or "")
        self.assertIn("out center tags", transport.query or "")
