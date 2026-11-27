import unittest
from unittest.mock import patch

import httpx

from Module.address_lookup import DadataAPI


class DadataAddressTests(unittest.TestCase):
    @patch("Module.address_lookup.Dadata")
    @patch("Module.address_lookup.os.getenv", return_value="test-token")
    def test_address_lookup_returns_normalized_address_and_coordinates(
        self, _getenv, dadata_type
    ):
        dadata_type.return_value.__enter__.return_value.clean.return_value = {
            "result": "Киров, улица Ленина, 10",
            "geo_lat": "58.60",
            "geo_lon": "49.66",
            "qc_geo": 0,
        }

        result = DadataAPI.check_address("Ленина, 10")

        self.assertEqual(
            result,
            {
                "address": "Киров, улица Ленина, 10",
                "geo_lat": "58.60",
                "geo_lon": "49.66",
                "qc_geo": 0,
            },
        )
        dadata_type.return_value.__enter__.return_value.clean.assert_called_once_with(
            "address",
            "Ленина, 10",
        )

    def test_empty_address_is_rejected_without_calling_dadata(self):
        with patch("Module.address_lookup.Dadata") as dadata_type:
            with self.assertRaises(ValueError):
                DadataAPI.check_address(" ")
        dadata_type.assert_not_called()

    @patch("Module.address_lookup.Dadata")
    @patch("Module.address_lookup.os.getenv", return_value=None)
    def test_missing_token_is_reported(self, _getenv, dadata_type):
        with self.assertRaisesRegex(RuntimeError, "DADATA_API"):
            DadataAPI.check_address("Ленина, 10")
        dadata_type.assert_not_called()

    @patch("Module.address_lookup.Dadata")
    @patch("Module.address_lookup.os.getenv", return_value="test-token")
    def test_api_network_failure_is_reported_for_manual_zone_fallback(
        self, _getenv, dadata_type
    ):
        dadata_type.return_value.__enter__.return_value.clean.side_effect = (
            httpx.ConnectError("offline")
        )

        with self.assertRaisesRegex(RuntimeError, "через DaData"):
            DadataAPI.check_address("Ленина, 10")


if __name__ == "__main__":
    unittest.main()
