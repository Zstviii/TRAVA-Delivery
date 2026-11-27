import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from Module.order_storage import OrderStore
from Module.zone_map import ZoneMapServer


class ZoneMapServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OrderStore(Path(self.temp_dir.name) / "orders.sqlite3")
        self.server = ZoneMapServer(self.store)

    def tearDown(self):
        self.server.close()
        self.temp_dir.cleanup()

    def test_map_page_and_zone_api_are_served_on_loopback(self):
        with urlopen(self.server.url, timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Зоны доставок", page)

        api_url = self.server.url.replace(
            f"/map/{self.server.token}",
            "/api/zones",
        )
        with urlopen(api_url, timeout=2) as response:
            self.assertEqual(json.load(response), {"zones": []})

        with self.assertRaises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{self.server.httpd.server_port}/api/zones")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_zone_api_saves_validated_geojson_to_sqlite(self):
        api_url = self.server.url.replace(
            f"/map/{self.server.token}",
            "/api/zones",
        )
        request = Request(
            api_url,
            data=json.dumps(
                {
                    "zones": [
                        {
                            "name": "Центр",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [49.0, 58.0],
                                        [50.0, 58.0],
                                        [50.0, 59.0],
                                        [49.0, 59.0],
                                    ]
                                ],
                            },
                        }
                    ]
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(request, timeout=2) as response:
            result = json.load(response)

        self.assertEqual(result["zones"][0]["name"], "Центр")
        self.assertIsInstance(result["zones"][0]["id"], int)
        self.assertEqual(len(self.store.list_delivery_zones()), 1)


if __name__ == "__main__":
    unittest.main()
