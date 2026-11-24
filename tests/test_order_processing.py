import unittest

from Module.order_processing import (
    EMPTY_VALUE,
    ORDER_COLUMN_HEADERS,
    format_address,
    prepare_orders,
)


class PrepareOrdersTests(unittest.TestCase):
    def test_orders_are_sorted_by_parsed_delivery_time_with_fallback(self):
        orders = [
            {
                "id": "later",
                "attributes": {"deliveryTimeFrom": "2026-11-24T11:00:00+03:00"},
            },
            {
                "id": "fallback",
                "attributes": {
                    "deliveryTimeFrom": None,
                    "dueTime": "2026-11-24T09:30:00+02:00",
                },
            },
            {
                "id": "earlier",
                "attributes": {"deliveryTimeFrom": "2026-11-24T10:00:00+03:00"},
            },
            {"id": "no-time", "attributes": {"deliveryTimeFrom": None}},
        ]

        rows = prepare_orders(orders)

        self.assertEqual(
            [row.order_id for row in rows],
            ["earlier", "fallback", "later", "no-time"],
        )

    def test_delivered_orders_are_hidden_and_null_status_is_kept(self):
        rows = prepare_orders(
            [
                {"id": "delivered", "attributes": {"deliveryStatus": "delivered"}},
                {"id": "unknown", "attributes": {"deliveryStatus": None}},
                {"id": "other", "attributes": {"deliveryStatus": "courier"}},
            ]
        )

        self.assertEqual([row.order_id for row in rows], ["unknown", "other"])
        self.assertEqual(rows[0].values[5], "Не указан")

    def test_missing_values_and_address_parts_have_safe_placeholders(self):
        rows = prepare_orders([{"id": "empty", "attributes": {}}])

        self.assertEqual(
            rows[0].values,
            (
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                "Не указан",
                EMPTY_VALUE,
            ),
        )
        self.assertEqual(format_address({"deliveryHouse": "7"}), "д. 7")
        self.assertEqual(format_address({}), EMPTY_VALUE)

    def test_address_uses_all_available_fields(self):
        order = {
            "attributes": {
                "deliveryCity": "Москва",
                "deliveryStreet": "Садовая",
                "deliveryHouse": "12",
                "deliveryBuilding": "2",
                "deliveryApartment": "34",
            }
        }

        self.assertEqual(
            format_address(order),
            "Москва, Садовая, д. 12, корп. 2, кв. 34",
        )

    def test_courier_assignment_uses_unique_order_id_not_document_number(self):
        rows = prepare_orders(
            [
                {
                    "id": "unique-id",
                    "attributes": {"docNo": "order-number"},
                }
            ],
            {"unique-id": "Анна", "order-number": "Не тот курьер"},
        )

        self.assertEqual(rows[0].values[6], "Анна")

    def test_headers_are_in_required_order_and_empty_input_is_safe(self):
        self.assertEqual(
            ORDER_COLUMN_HEADERS,
            (
                "Номер заказа",
                "Получатель",
                "Номер телефона",
                "Адрес",
                "Время доставки",
                "Статус доставки",
                "Курьер",
            ),
        )
        self.assertEqual(prepare_orders([]), [])


if __name__ == "__main__":
    unittest.main()
