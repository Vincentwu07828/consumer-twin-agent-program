"""Nearby places use Amap facts without inventing price, opening, or allergy claims."""
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from consumer_behavior.amap_places import AmapNearbyProvider
from consumer_behavior.research_api import ConsumerResearchAPI


class AmapPlacesTests(unittest.TestCase):
    def test_gps_conversion_then_nearby_search_preserves_unknowns(self):
        calls = []

        def fake_get(url):
            parsed = urlparse(url)
            calls.append((parsed.path, parse_qs(parsed.query)))
            if parsed.path.endswith("/convert"):
                return {"status": "1", "locations": "116.400000,39.900000"}
            return {"status": "1", "pois": [
                {"id": "B001", "name": "街角面馆", "address": "和平路1号", "distance": "350",
                 "type": "餐饮服务;中餐厅", "biz_ext": {"cost": "42"}},
                {"id": "B002", "name": "小店", "address": [], "distance": "680",
                 "type": "餐饮服务", "biz_ext": {"cost": []}},
            ]}

        result = AmapNearbyProvider("test-key", http_get=fake_get).search(
            longitude=116.39, latitude=39.89, category="restaurant")
        self.assertEqual(calls[0][0], "/v3/assistant/coordinate/convert")
        self.assertEqual(calls[0][1]["coordsys"], ["gps"])
        self.assertEqual(calls[1][1]["location"], ["116.400000,39.900000"])
        self.assertEqual(calls[1][1]["types"], ["050000"])
        self.assertEqual(calls[1][1]["extensions"], ["all"])
        self.assertEqual(result["items"][0]["average_cost_cny"], 42)
        self.assertIsNone(result["items"][1]["average_cost_cny"])
        self.assertEqual(result["items"][0]["opening_status"], "unknown")
        self.assertFalse(result["items"][0]["ingredients_verified"])

    def test_rejects_invalid_location_before_network_or_secret_exposure(self):
        provider = AmapNearbyProvider("test-key", http_get=lambda _: self.fail("unexpected network"))
        for longitude, latitude in ((True, 39.9), (float("nan"), 39.9), (181, 39.9)):
            with self.subTest(longitude=longitude), self.assertRaises(ValueError):
                provider.search(longitude=longitude, latitude=latitude)
        with self.assertRaisesRegex(RuntimeError, "配置 amap_web_service_key"):
            AmapNearbyProvider("").search(longitude=116.4, latitude=39.9)

    def test_research_api_routes_location_without_session(self):
        class Service:
            def nearby(self, **kwargs):
                return kwargs
        result = ConsumerResearchAPI(Service()).dispatch("POST", "/api/research/nearby",
            {"longitude": 116.4, "latitude": 39.9, "category": "shopping"})
        self.assertEqual(result["category"], "shopping")
        self.assertEqual(result["radius"], 2000)


if __name__ == "__main__":
    unittest.main()
