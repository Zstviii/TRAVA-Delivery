import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from Module.order_storage import OrderStore


class OrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "orders.sqlite3"
        self.store = OrderStore(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_order(self, **overrides):
        values = {
            "recipient": "Иван",
            "phone": "+7 900 000-00-00",
            "street_address": "Ленина, 10",
            "floor": "3",
            "apartment": "12",
            "intercom": "12К",
            "order_date": date(2026, 11, 27),
            "delivery_time": "12:30",
            "comment": "Позвонить заранее",
        }
        values.update(overrides)
        return self.store.create_order(**values)

    def test_orders_are_saved_and_assigned_sequential_numbers(self):
        first_id = self.create_order()
        second_id = self.create_order(
            order_date=date(2026, 11, 28),
            recipient="Мария",
        )

        self.assertEqual(first_id, 1)
        self.assertEqual(second_id, 2)
        self.assertEqual(
            self.store.list_orders(date(2026, 11, 27))[0]["order_no"],
            "ORD-000001",
        )
        self.assertEqual(
            self.store.list_orders(date(2026, 11, 28))[0]["order_no"],
            "ORD-000002",
        )

    def test_delivery_time_range_is_saved_and_validated(self):
        order_id = self.create_order(
            delivery_time="10:00",
            delivery_time_end="12:30",
        )
        order = self.store.get_order(order_id)
        self.assertEqual(order["delivery_time"], "10:00")
        self.assertEqual(order["delivery_time_end"], "12:30")

        with self.assertRaisesRegex(ValueError, "начало"):
            self.create_order(delivery_time="", delivery_time_end="12:30")
        with self.assertRaisesRegex(ValueError, "раньше"):
            self.create_order(delivery_time="14:00", delivery_time_end="12:00")

    def test_order_fields_persist_and_courier_assignment_updates_phone(self):
        order_id = self.create_order()
        self.store.add_courier("Курьер", "+7 911 111-11-11")
        self.store.assign_courier(order_id, "+7 911 111-11-11")

        saved = self.store.list_orders(date(2026, 11, 27))[0]
        self.assertEqual(saved["floor"], "3")
        self.assertEqual(saved["apartment"], "12")
        self.assertEqual(saved["intercom"], "12К")
        self.assertEqual(saved["comment"], "Позвонить заранее")
        self.assertEqual(saved["courier_phone"], "+7 911 111-11-11")
        self.assertEqual(saved["status"], "Взят курьером")

        self.store.assign_courier(order_id, None)
        unassigned = self.store.list_orders(date(2026, 11, 27))[0]
        self.assertIsNone(unassigned["courier_phone"])
        self.assertEqual(unassigned["status"], "Новый")

    def test_delivery_zone_matching_and_manual_order_assignment(self):
        zone = self.store.replace_delivery_zones(
            [
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
        )[0]
        self.assertEqual(
            self.store.detect_delivery_zone(49.5, 58.5),
            (zone["id"], "matched"),
        )
        self.assertEqual(self.store.detect_delivery_zone(51, 58.5), (None, "outside"))
        self.assertEqual(self.store.detect_delivery_zone(49, 58.5), (None, "ambiguous"))

        order_id = self.create_order()
        self.store.add_courier("Курьер", "+7 911 111-11-11")
        self.store.update_order_assignment(
            order_id,
            "+7 911 111-11-11",
            zone["id"],
        )
        saved = self.store.get_order(order_id)
        self.assertEqual(saved["zone_name"], "Центр")
        self.assertEqual(saved["zone_source"], "manual")
        self.assertEqual(saved["courier_name"], "Курьер")
        self.assertEqual(saved["status"], "Взят курьером")

    def test_zone_match_becomes_ambiguous_when_polygons_overlap(self):
        self.store.replace_delivery_zones(
            [
                {
                    "name": name,
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
                for name in ("Центр", "Север")
            ]
        )

        self.assertEqual(self.store.detect_delivery_zone(49.5, 58.5), (None, "ambiguous"))

    def test_polygon_holes_are_excluded_and_invalid_geometry_is_rejected(self):
        zone = {
            "name": "Парк",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [49.0, 58.0],
                        [50.0, 58.0],
                        [50.0, 59.0],
                        [49.0, 59.0],
                    ],
                    [
                        [49.4, 58.4],
                        [49.6, 58.4],
                        [49.6, 58.6],
                        [49.4, 58.6],
                    ],
                ],
            },
        }
        self.store.replace_delivery_zones([zone])
        self.assertEqual(self.store.detect_delivery_zone(49.2, 58.2)[1], "matched")
        self.assertEqual(self.store.detect_delivery_zone(49.5, 58.5), (None, "outside"))

        zone["geometry"] = {"type": "Point", "coordinates": [49.5, 58.5]}
        with self.assertRaisesRegex(ValueError, "GeoJSON Polygon"):
            self.store.replace_delivery_zones([zone])
        self.assertEqual(len(self.store.list_delivery_zones()), 1)

    def test_courier_status_updates_are_sequential_and_bound_to_linked_account(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 67890, "courier")
        order_id = self.create_order()
        self.store.assign_courier(order_id, "+7 911 111-11-11")

        with self.assertRaisesRegex(ValueError, "не назначен вашему аккаунту"):
            self.store.set_courier_order_status(
                order_id, 12345, 99999, "Доставляется"
            )

        in_transit = self.store.set_courier_order_status(
            order_id, 12345, 67890, "Доставляется"
        )
        self.assertTrue(in_transit["changed"])
        self.assertEqual(in_transit["order"]["status"], "Доставляется")
        delivered = self.store.set_courier_order_status(
            order_id, 12345, 67890, "Доставлен"
        )
        self.assertTrue(delivered["changed"])
        self.assertEqual(delivered["order"]["status"], "Доставлен")
        self.assertIsNotNone(delivered["order"]["delivered_at"])
        self.assertFalse(
            self.store.set_courier_order_status(
                order_id, 12345, 67890, "Доставлен"
            )["changed"]
        )
        self.assertEqual(self.store.list_orders(date(2026, 11, 27)), [])

    def test_couriers_can_be_assigned_to_multiple_zones(self):
        zones = self.store.replace_delivery_zones(
            [
                {
                    "name": name,
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
                for name in ("Центр", "Север")
            ]
        )
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        another_courier_id = self.store.add_courier(
            "Второй курьер",
            "+7 922 222-22-22",
        )

        self.store.set_courier_zones(courier_id, [zones[0]["id"], zones[1]["id"]])
        self.store.set_courier_zones(another_courier_id, [zones[0]["id"]])

        couriers = {
            courier["id"]: courier for courier in self.store.list_couriers()
        }
        self.assertEqual(
            set(couriers[courier_id]["zone_ids"]),
            {zone["id"] for zone in zones},
        )
        zones_with_couriers = self.store.list_delivery_zones_with_couriers()
        self.assertEqual(
            {courier["id"] for courier in zones_with_couriers[0]["couriers"]},
            {courier_id, another_courier_id},
        )
        self.assertEqual(
            [courier["id"] for courier in zones_with_couriers[1]["couriers"]],
            [courier_id],
        )

        self.store.set_courier_zones(courier_id, [zones[1]["id"]])
        updated_couriers = {
            courier["id"]: courier for courier in self.store.list_couriers()
        }
        self.assertEqual(updated_couriers[courier_id]["zone_ids"], [zones[1]["id"]])
        updated_zones = self.store.list_delivery_zones_with_couriers()
        self.assertEqual(
            [courier["id"] for courier in updated_zones[0]["couriers"]],
            [another_courier_id],
        )

    def test_delivery_history_amounts_and_paid_status_are_persisted(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 67890, "courier")
        order_id = self.create_order()
        self.store.assign_courier(order_id, "+7 911 111-11-11")
        self.store.set_courier_order_status(
            order_id, 12345, 67890, "Доставляется"
        )
        self.store.set_courier_order_status(
            order_id, 12345, 67890, "Доставлен"
        )

        delivered_order = self.store.get_order(order_id)
        delivered_day = date.fromisoformat(delivered_order["delivered_at"][:10])
        history = self.store.list_courier_delivery_history(courier_id, delivered_day)
        self.assertEqual([row["id"] for row in history], [order_id])
        self.assertIsNone(history[0]["delivery_fee_kopecks"])
        self.assertEqual(history[0]["is_paid"], 0)
        self.assertEqual(
            self.store.list_courier_delivery_history(
                courier_id,
                delivered_day + timedelta(days=1),
            ),
            [],
        )

        with self.assertRaisesRegex(ValueError, "Сначала укажите"):
            self.store.set_courier_delivery_paid(order_id, courier_id, True)
        with self.assertRaises(ValueError):
            self.store.save_courier_delivery_amounts(
                courier_id,
                delivered_day,
                {order_id: -1},
            )

        self.store.save_courier_delivery_amounts(
            courier_id,
            delivered_day,
            {order_id: 12550},
        )
        self.store.set_courier_delivery_paid(order_id, courier_id, True)
        paid_order = self.store.list_courier_delivery_history(
            courier_id,
            delivered_day,
        )[0]
        self.assertEqual(paid_order["delivery_fee_kopecks"], 12550)
        self.assertEqual(paid_order["is_paid"], 1)
        self.assertIsNotNone(paid_order["paid_at"])

        with self.assertRaisesRegex(ValueError, "оплаченного"):
            self.store.save_courier_delivery_amounts(
                courier_id,
                delivered_day,
                {order_id: None},
            )
        self.store.set_courier_delivery_paid(order_id, courier_id, False)
        self.store.save_courier_delivery_amounts(
            courier_id,
            delivered_day,
            {order_id: None},
        )
        unpaid_order = self.store.list_courier_delivery_history(
            courier_id,
            delivered_day,
        )[0]
        self.assertIsNone(unpaid_order["delivery_fee_kopecks"])
        self.assertEqual(unpaid_order["is_paid"], 0)
        self.assertIsNone(unpaid_order["paid_at"])

    def test_required_order_data_and_duplicate_couriers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.create_order(street_address=" ")

        self.store.add_courier("Курьер", "+7 911 111-11-11")
        with self.assertRaises(ValueError):
            self.store.add_courier("Другой курьер", "+7 911 111-11-11")

    def test_telegram_link_code_is_one_time_and_links_courier_chat(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)

        self.store.link_telegram_account(code, 12345, 67890, "courier_user")

        courier = self.store.get_courier_by_telegram_chat_id(12345)
        self.assertEqual(courier["name"], "Курьер")
        self.assertEqual(courier["telegram_user_id"], "67890")
        self.assertEqual(courier["telegram_username"], "courier_user")
        with self.assertRaisesRegex(ValueError, "не найден или уже использован"):
            self.store.link_telegram_account(code, 12345, 67890, "courier_user")

    def test_courier_orders_include_today_and_future_assignments(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 67890, "courier_user")

        today_order_id = self.create_order(order_date=date.today())
        future_order_id = self.create_order(
            order_date=date.today() + timedelta(days=1),
            recipient="Мария",
        )
        delivered_order_id = self.create_order(
            order_date=date.today(),
            recipient="Уже доставлен",
        )
        self.create_order(order_date=date.today() - timedelta(days=1))
        self.store.assign_courier(today_order_id, "+7 911 111-11-11")
        self.store.assign_courier(future_order_id, "+7 911 111-11-11")
        self.store.assign_courier(delivered_order_id, "+7 911 111-11-11")
        self.store.set_courier_order_status(
            delivered_order_id,
            12345,
            67890,
            "Доставляется",
        )
        self.store.set_courier_order_status(
            delivered_order_id,
            12345,
            67890,
            "Доставлен",
        )

        orders = self.store.list_current_orders_for_telegram(12345)

        self.assertEqual(
            [order["recipient"] for order in orders],
            ["Иван", "Мария"],
        )
        self.assertEqual(orders[0]["apartment"], "12")
        self.assertEqual(orders[0]["intercom"], "12К")

    def test_existing_courier_database_is_migrated_for_telegram_fields(self):
        legacy_database_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE couriers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE
                )
                """
            )

        migrated_store = OrderStore(legacy_database_path)
        courier_id = migrated_store.add_courier("Старый курьер", "+7 922 222-22-22")
        code = migrated_store.create_telegram_link_code(courier_id)
        migrated_store.link_telegram_account(code, 222, 333, None)

        self.assertEqual(
            migrated_store.get_courier_by_telegram_chat_id(222)["name"],
            "Старый курьер",
        )

    def test_existing_orders_are_migrated_without_losing_assignments(self):
        legacy_database_path = Path(self.temp_dir.name) / "legacy-orders.sqlite3"
        with closing(sqlite3.connect(legacy_database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE couriers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE
                );
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_no TEXT UNIQUE,
                    recipient TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    street_address TEXT NOT NULL,
                    floor TEXT NOT NULL DEFAULT '',
                    apartment TEXT NOT NULL DEFAULT '',
                    intercom TEXT NOT NULL DEFAULT '',
                    order_date TEXT NOT NULL,
                    delivery_time TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Новый',
                    comment TEXT NOT NULL DEFAULT '',
                    courier_id INTEGER REFERENCES couriers(id)
                );
                INSERT INTO couriers (name, phone)
                VALUES ('Старый курьер', '+7 933 333-33-33');
                INSERT INTO orders (
                    order_no, recipient, phone, street_address, order_date,
                    courier_id
                ) VALUES (
                    'ORD-000001', 'Клиент', '+7 900 000-00-00', 'Ленина, 10',
                    '2026-11-27', 1
                );
                """
            )

        migrated_store = OrderStore(legacy_database_path)
        saved = migrated_store.list_orders(date(2026, 11, 27))[0]
        self.assertEqual(saved["status"], "Взят курьером")
        self.assertEqual(saved["courier_name"], "Старый курьер")
        self.assertIsNone(saved["zone_name"])

    def test_six_digit_link_code_attempts_are_locked_after_five_failures(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.assertRegex(code, r"^[0-9]{6}$")

        for expected_attempt in range(1, 5):
            attempts, lockout = self.store.record_telegram_link_failure(12345)
            self.assertEqual(attempts, expected_attempt)
            self.assertIsNone(lockout)

        attempts, lockout = self.store.record_telegram_link_failure(12345)
        self.assertEqual(attempts, 5)
        self.assertGreaterEqual(lockout, 14)
        self.assertIsNotNone(self.store.telegram_link_lockout_remaining(12345))

        self.store.reset_telegram_link_attempts(12345)
        self.assertIsNone(self.store.telegram_link_lockout_remaining(12345))


if __name__ == "__main__":
    unittest.main()
