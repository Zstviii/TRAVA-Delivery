import unittest

from Module.order_processing import (
    ADDRESS_PREFIX,
    EMPTY_VALUE,
    ORDER_COLUMN_HEADERS,
    format_address,
    prepare_orders,
)


class PrepareOrdersTests(unittest.TestCase):
    def test_orders_are_sorted_by_delivery_time_and_missing_time_goes_last(self):
        rows = prepare_orders(
            [
                {"id": 3, "delivery_time": ""},
                {"id": 2, "delivery_time": "12:30"},
                {"id": 1, "delivery_time": "09:15"},
            ]
        )

        self.assertEqual([row.order_id for row in rows], ["1", "2", "3"])

    def test_orders_with_single_digit_hours_sort_by_time_of_day(self):
        rows = prepare_orders(
            [
                {"id": 10, "delivery_time": "10:00"},
                {"id": 12, "delivery_time": "8:30"},
                {"id": 11, "delivery_time": "9:00"},
            ]
        )

        self.assertEqual([row.order_id for row in rows], ["12", "11", "10"])

    def test_order_row_contains_zone_comment_and_assigned_courier_name(self):
        rows = prepare_orders(
            [
                {
                    "id": 1,
                    "order_no": "ORD-000001",
                    "recipient": "Иван",
                    "phone": "+7 900 000-00-00",
                    "street_address": "Ленина, 10",
                    "zone_name": "Центр",
                    "delivery_time": "14:00",
                    "status": "Новый",
                    "comment": "Позвонить заранее",
                    "courier_name": "Алексей",
                    "courier_phone": "+7 911 111-11-11",
                }
            ]
        )
        self.assertEqual(
            rows[0].values,
            (
                "ORD-000001",
                "Иван",
                "+7 900 000-00-00",
                f"{ADDRESS_PREFIX}, Ленина, 10",
                "Центр",
                "14:00",
                "Новый",
                "Позвонить заранее",
                "Алексей",
            ),
        )

    def test_delivery_time_range_is_shown_in_orders_table(self):
        rows = prepare_orders(
            [
                {
                    "id": 1,
                    "delivery_time": "10:00",
                    "delivery_time_end": "12:30",
                }
            ]
        )

        self.assertEqual(rows[0].values[5], "10:00–12:30")

    def test_address_prefix_is_added_and_internal_address_details_are_not_shown(self):
        self.assertEqual(
            format_address({"street_address": "Свободы, 12"}),
            f"{ADDRESS_PREFIX}, Свободы, 12",
        )
        self.assertEqual(format_address({"street_address": ""}), EMPTY_VALUE)

    def test_missing_fields_have_placeholders(self):
        rows = prepare_orders([{"id": 1}])
        self.assertEqual(
            rows[0].values,
            (
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                EMPTY_VALUE,
                "Не указан",
                EMPTY_VALUE,
                EMPTY_VALUE,
            ),
        )

    def test_headers_include_zone_comment_and_courier_name(self):
        self.assertEqual(
            ORDER_COLUMN_HEADERS,
            (
                "Номер заказа",
                "Получатель",
                "Номер телефона",
                "Адрес",
                "Зона",
                "Время доставки",
                "Статус доставки",
                "Комментарий",
                "Курьер",
            ),
        )


if __name__ == "__main__":
    unittest.main()
