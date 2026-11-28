from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import Mock, patch

import httpx

from Module.address_lookup import (
    DadataAPI,
    _run_address_diagnostic,
    resolve_delivery_address,
)


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

        with self.assertRaisesRegex(RuntimeError, "ConnectError"):
            DadataAPI.check_address("Ленина, 10")

    @patch("Module.address_lookup.Dadata")
    @patch("Module.address_lookup.os.getenv", return_value="test-token")
    def test_api_http_failure_reports_status_without_response_body(
        self, _getenv, dadata_type
    ):
        request = httpx.Request("POST", "https://cleaner.dadata.ru/api/v1/clean/address")
        response = httpx.Response(401, request=request, text="unauthorized")
        dadata_type.return_value.__enter__.return_value.clean.side_effect = (
            httpx.HTTPStatusError(
                "Unauthorized",
                request=request,
                response=response,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            DadataAPI.check_address("Ленина, 10")

    @patch(
        "Module.address_lookup.DadataAPI.check_address",
        return_value={
            "address": "Киров, улица Ленина, 10",
            "geo_lat": "58.60",
            "geo_lon": "49.66",
            "qc_geo": 0,
        },
    )
    def test_delivery_lookup_uses_city_context_and_detects_zone(
        self, check_address
    ):
        detect_zone = Mock(return_value=(7, "matched"))

        result = resolve_delivery_address("Ленина, 10", detect_zone)

        self.assertEqual(result.zone_id, 7)
        self.assertEqual(result.zone_detection_status, "matched")
        self.assertEqual(result.latitude, 58.6)
        self.assertEqual(result.longitude, 49.66)
        check_address.assert_called_once_with(
            "Кировская область, Киров, Ленина, 10"
        )
        detect_zone.assert_called_once_with(49.66, 58.6)

    @patch(
        "Module.address_lookup.DadataAPI.check_address",
        return_value={
            "address": "Киров, улица Ленина",
            "geo_lat": "58.60",
            "geo_lon": "49.66",
            "qc_geo": 2,
        },
    )
    def test_delivery_lookup_does_not_assign_zone_from_low_accuracy(
        self, _check_address
    ):
        detect_zone = Mock()

        result = resolve_delivery_address("Ленина", detect_zone)

        self.assertIsNone(result.zone_id)
        self.assertEqual(result.zone_detection_status, "low_accuracy")
        self.assertIsNone(result.latitude)
        detect_zone.assert_not_called()

    def test_address_diagnostic_prints_lookup_and_zone_result(self):
        response = {
            "address": "Киров, улица Калинина, 9",
            "geo_lat": "58.60",
            "geo_lon": "49.66",
            "qc_geo": 0,
        }
        with (
            patch("builtins.input", return_value="Калинина 9"),
            patch(
                "Module.address_lookup.DadataAPI.check_address",
                return_value=response,
            ) as check_address,
            patch("Module.address_lookup.OrderStore") as order_store,
        ):
            order_store.return_value.detect_delivery_zone = Mock(
                return_value=(18, "matched")
            )
            order_store.return_value.list_delivery_zones.return_value = [
                {"id": 18, "name": "Центр"}
            ]
            output = StringIO()
            with redirect_stdout(output):
                result = _run_address_diagnostic()

        self.assertEqual(result, 0)
        self.assertIn("Нормализованный адрес: Киров, улица Калинина, 9", output.getvalue())
        self.assertIn("Итог: зона определена — Центр (ID 18)", output.getvalue())
        self.assertNotIn("DADATA_API", output.getvalue())
        check_address.assert_called_once_with(
            "Кировская область, Киров, Калинина 9"
        )


if __name__ == "__main__":
    unittest.main()
