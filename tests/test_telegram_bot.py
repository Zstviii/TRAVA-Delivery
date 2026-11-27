import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from Module.order_storage import OrderStore
from Module.telegram_bot import TelegramBot


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OrderStore(Path(self.temp_dir.name) / "orders.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_start_command_links_courier_and_shows_orders_keyboard(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.assertRegex(code, r"^[0-9]{6}$")
        on_linked = Mock()
        bot = TelegramBot("test-token", self.store, on_linked)
        start_update = {
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "username": "courier"},
                "text": "/start",
            }
        }
        code_update = {
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "username": "courier"},
                "text": code,
            }
        }

        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(start_update)
            self.assertIn("6-значный код", api_call.call_args.args[1]["text"])
            bot._handle_update(code_update)

        self.assertEqual(
            self.store.get_courier_by_telegram_chat_id(12345)["name"],
            "Курьер",
        )
        self.assertEqual(api_call.call_count, 2)
        self.assertEqual(api_call.call_args.args[0], "sendMessage")
        on_linked.assert_called_once_with()
        self.assertEqual(
            api_call.call_args.args[1]["reply_markup"]["keyboard"],
            [[{"text": "Заказы"}]],
        )

    def test_orders_button_only_returns_orders_assigned_to_linked_courier(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 12345, "courier")
        order_id = self.store.create_order(
            recipient="Получатель",
            phone="+7 900 000-00-00",
            street_address="Ленина, 10",
            floor="2",
            apartment="15",
            order_date=date.today(),
            delivery_time="12:30",
            comment="Позвонить",
        )
        self.store.assign_courier(order_id, "+7 911 111-11-11")
        other_courier_id = self.store.add_courier(
            "Другой курьер",
            "+7 922 222-22-22",
        )
        other_code = self.store.create_telegram_link_code(other_courier_id)
        self.store.link_telegram_account(other_code, 67890, 67890, "other")
        other_order_id = self.store.create_order(
            recipient="Чужой получатель",
            phone="+7 999 999-99-99",
            street_address="Московская, 1",
            order_date=date.today(),
        )
        self.store.assign_courier(other_order_id, "+7 922 222-22-22")
        bot = TelegramBot("test-token", self.store)

        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(
                {
                    "message": {
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345},
                        "text": "Заказы",
                    }
                }
            )

        text = api_call.call_args.args[1]["text"]
        self.assertIn("ORD-000001", text)
        self.assertIn("Квартира: 15", text)
        self.assertIn("Комментарий: Позвонить", text)
        self.assertIn("Статус: Взят курьером", text)
        self.assertEqual(
            api_call.call_args.args[1]["reply_markup"]["inline_keyboard"][0][0][
                "text"
            ],
            "Доставляется",
        )
        self.assertNotIn("Чужой получатель", text)

    def test_order_status_buttons_update_order_and_edit_the_existing_message(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 12345, "courier")
        order_id = self.store.create_order(
            recipient="Получатель",
            phone="+7 900 000-00-00",
            street_address="Ленина, 10",
            order_date=date.today(),
        )
        self.store.assign_courier(order_id, "+7 911 111-11-11")
        on_order_updated = Mock()
        bot = TelegramBot(
            "test-token",
            self.store,
            on_order_updated=on_order_updated,
        )
        original_order = self.store.get_order(order_id)
        original_message = {
            "message_id": 88,
            "chat": {"id": 12345},
            "text": bot._format_order(original_order),
            "reply_markup": bot._order_keyboard(original_order),
        }

        transit_update = {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 12345},
                "message": original_message,
                "data": f"order:{order_id}:in_transit",
            }
        }
        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(transit_update)

        self.assertEqual(api_call.call_args_list[0].args[0], "answerCallbackQuery")
        edited_message = api_call.call_args_list[1].args[1]
        self.assertEqual(api_call.call_args_list[1].args[0], "editMessageText")
        self.assertEqual(edited_message["message_id"], 88)
        self.assertIn("Статус: Доставляется", edited_message["text"])
        self.assertEqual(
            edited_message["reply_markup"]["inline_keyboard"][0][0]["text"],
            "Доставлен",
        )
        self.assertEqual(
            self.store.get_order(order_id)["status"],
            "Доставляется",
        )
        on_order_updated.assert_called_once_with()

        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(
                {
                    "callback_query": {
                        "id": "callback-retry",
                        "from": {"id": 12345},
                        "message": original_message,
                        "data": f"order:{order_id}:in_transit",
                    }
                }
            )
        self.assertEqual(api_call.call_args_list[1].args[0], "editMessageText")
        self.assertEqual(
            api_call.call_args_list[1].args[1]["reply_markup"]["inline_keyboard"][0][
                0
            ]["text"],
            "Доставлен",
        )
        on_order_updated.assert_called_once_with()

        transit_order = self.store.get_order(order_id)
        delivered_update = {
            "callback_query": {
                "id": "callback-2",
                "from": {"id": 12345},
                "message": {
                    "message_id": 88,
                    "chat": {"id": 12345},
                    "text": bot._format_order(transit_order),
                    "reply_markup": bot._order_keyboard(transit_order),
                },
                "data": f"order:{order_id}:delivered",
            }
        }
        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(delivered_update)

        self.assertIn("Статус: Доставлен", api_call.call_args_list[1].args[1]["text"])
        self.assertEqual(
            api_call.call_args_list[1].args[1]["reply_markup"],
            {"inline_keyboard": []},
        )
        self.assertEqual(self.store.get_order(order_id)["status"], "Доставлен")

    def test_order_status_callback_rejects_another_telegram_account(self):
        courier_id = self.store.add_courier("Курьер", "+7 911 111-11-11")
        code = self.store.create_telegram_link_code(courier_id)
        self.store.link_telegram_account(code, 12345, 12345, "courier")
        order_id = self.store.create_order(
            recipient="Получатель",
            phone="+7 900 000-00-00",
            street_address="Ленина, 10",
            order_date=date.today(),
        )
        self.store.assign_courier(order_id, "+7 911 111-11-11")
        bot = TelegramBot("test-token", self.store)

        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(
                {
                    "callback_query": {
                        "id": "callback-1",
                        "from": {"id": 99999},
                        "message": {"message_id": 88, "chat": {"id": 67890}},
                        "data": f"order:{order_id}:in_transit",
                    }
                }
            )

        self.assertEqual(self.store.get_order(order_id)["status"], "Взят курьером")
        self.assertEqual(api_call.call_args.args[0], "answerCallbackQuery")
        self.assertIn("не назначен", api_call.call_args.args[1]["text"])

    def test_linking_from_group_chat_is_rejected(self):
        bot = TelegramBot("test-token", self.store)
        with patch.object(bot, "_api_call") as api_call:
            bot._handle_update(
                {
                    "message": {
                        "chat": {"id": -100, "type": "group"},
                        "from": {"id": 12345},
                        "text": "/start 12345678",
                    }
                }
            )

        self.assertIn("личный чат", api_call.call_args.args[1]["text"])


if __name__ == "__main__":
    unittest.main()
