import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import requests
from dotenv import load_dotenv

from Module.order_processing import ADDRESS_PREFIX
from Module.order_storage import OrderStore

load_dotenv(Path(__file__).resolve().parent.parent / "Config" / ".env")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
ORDERS_BUTTON = "Заказы"
MESSAGE_LIMIT = 3500


class TelegramConflictError(RuntimeError):
    pass


class TelegramAPIError(RuntimeError):
    pass


class TelegramBot:
    def __init__(
        self,
        token: str,
        order_store: OrderStore,
        on_courier_linked: Callable[[], None] | None = None,
        on_order_updated: Callable[[], None] | None = None,
    ):
        self.token = token
        self.order_store = order_store
        self.session = requests.Session()
        self.offset = 0
        self.username = None
        self.on_courier_linked = on_courier_linked
        self.on_order_updated = on_order_updated
        self.pending_link_chats: dict[str, datetime] = {}

    def _api_call(self, method: str, payload: dict[str, object] | None = None):
        response = self.session.post(
            TELEGRAM_API_URL.format(token=self.token, method=method),
            json=payload or {},
            timeout=(5, 35),
        )
        if response.status_code == 409:
            raise TelegramConflictError
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, Mapping) or not result.get("ok") or "result" not in result:
            raise TelegramAPIError
        return result["result"]

    def run(
        self,
        stop_event: threading.Event,
        on_status: Callable[[str, bool], None],
    ) -> None:
        bot_username = None
        connected = False
        while not stop_event.is_set():
            try:
                if bot_username is None:
                    bot_info = self._api_call("getMe")
                    bot_username = bot_info.get("username")
                    self.username = bot_username
                    label = (
                        f"Telegram подключён: @{bot_username}"
                        if bot_username
                        else "Telegram подключён"
                    )
                    on_status(label, True)
                    connected = True

                updates = self._api_call(
                    "getUpdates",
                    {"offset": self.offset, "timeout": 25},
                )
                if not connected:
                    label = (
                        f"Telegram подключён: @{bot_username}"
                        if bot_username
                        else "Telegram подключён"
                    )
                    on_status(label, True)
                    connected = True
                for update in updates:
                    if stop_event.is_set():
                        return
                    self.offset = update["update_id"] + 1
                    try:
                        self._handle_update(update)
                    except (ValueError, sqlite3.Error):
                        message = update.get("message", {})
                        chat = message.get("chat", {})
                        if chat.get("id") is not None:
                            self._send_message(
                                chat["id"],
                                "Не удалось обработать запрос. Попробуйте ещё раз.",
                            )
            except TelegramConflictError:
                on_status(
                    "Бот уже используется в другом процессе или через webhook",
                    False,
                )
                return
            except (requests.RequestException, TelegramAPIError, ValueError):
                connected = False
                on_status("Не удалось подключиться к Telegram", False)
                stop_event.wait(5)

    def _handle_update(self, update: Mapping[str, object]) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, Mapping):
            self._handle_callback_query(callback_query)
            return

        message = update.get("message")
        if not isinstance(message, Mapping):
            return

        chat = message.get("chat")
        sender = message.get("from")
        text = message.get("text")
        if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
            return
        if not isinstance(text, str):
            return

        chat_id = chat.get("id")
        user_id = sender.get("id")
        if chat_id is None or user_id is None:
            return
        if chat.get("type") != "private":
            self._send_message(
                chat_id,
                "Откройте личный чат с ботом, чтобы подключить аккаунт "
                "и просматривать заказы.",
            )
            return

        parts = text.strip().split(maxsplit=1)
        command = parts[0].split("@", maxsplit=1)[0].lower() if parts else ""
        if command == "/start":
            now = datetime.now(timezone.utc)
            self.pending_link_chats = {
                key: expiry
                for key, expiry in self.pending_link_chats.items()
                if expiry > now
            }
            self.pending_link_chats[str(chat_id)] = now + timedelta(minutes=15)
            self._send_message(
                chat_id,
                "Введите 6-значный код подключения, который выдал администратор. "
                "Если аккаунт уже подключён, нажмите «Заказы».",
                show_orders_button=True,
            )
            return

        if command == "/orders" or text.strip().casefold() == ORDERS_BUTTON.casefold():
            self._send_orders(chat_id)
            return

        chat_key = str(chat_id)
        pending_until = self.pending_link_chats.get(chat_key)
        if pending_until is not None:
            if pending_until <= datetime.now(timezone.utc):
                self.pending_link_chats.pop(chat_key, None)
                self._send_message(chat_id, "Время ввода кода истекло. Отправьте /start.")
                return

            if not re.fullmatch(r"[0-9]{6}", text.strip()):
                self._send_message(chat_id, "Код должен состоять ровно из 6 цифр.")
                return

            lockout = self.order_store.telegram_link_lockout_remaining(chat_id)
            if lockout is not None:
                self.pending_link_chats.pop(chat_key, None)
                self._send_message(
                    chat_id,
                    f"Слишком много попыток. Попробуйте через {lockout} мин.",
                )
                return

            username = sender.get("username")
            try:
                self.order_store.link_telegram_account(
                    text.strip(),
                    chat_id,
                    user_id,
                    username if isinstance(username, str) else None,
                )
            except ValueError as error:
                attempts, lockout = self.order_store.record_telegram_link_failure(
                    chat_id
                )
                if lockout is None:
                    self._send_message(
                        chat_id,
                        f"{error} Осталось попыток: "
                        f"{5 - attempts}.",
                    )
                else:
                    self.pending_link_chats.pop(chat_key, None)
                    self._send_message(
                        chat_id,
                        f"Слишком много попыток. Попробуйте через {lockout} мин.",
                    )
                return

            self.order_store.reset_telegram_link_attempts(chat_id)
            self.pending_link_chats.pop(chat_key, None)
            if self.on_courier_linked is not None:
                self.on_courier_linked()
            self._send_message(
                chat_id,
                "Аккаунт курьера подключён. Нажмите «Заказы», чтобы увидеть "
                "назначенные вам актуальные заказы.",
                show_orders_button=True,
            )
            return

        self._send_message(
            chat_id,
            "Сначала отправьте /start, чтобы подключить аккаунт. "
            "Для просмотра заказов используйте кнопку «Заказы».",
        )

    def _send_orders(self, chat_id: int | str) -> None:
        courier = self.order_store.get_courier_by_telegram_chat_id(chat_id)
        if courier is None:
            self._send_message(
                chat_id,
                "Telegram-аккаунт не подключён. Запросите код у администратора.",
            )
            return

        orders = self.order_store.list_current_orders_for_telegram(chat_id)
        if not orders:
            self._send_message(chat_id, "Сейчас у вас нет актуальных заказов.")
            return

        self._send_message(
            chat_id,
            f"Актуальные заказы для {str(courier['name'])[:100]}:",
        )
        for order in orders:
            self._send_order_message(chat_id, order)

    @staticmethod
    def _format_order(order: Mapping[str, object]) -> str:
        address_parts = [
            ADDRESS_PREFIX,
            str(order["street_address"]),
        ]
        details = [
            f"Заказ {order['order_no']}",
            f"Дата и время: {order['order_date']} {order['delivery_time']}".strip(),
            f"Статус: {order['status']}",
            f"Получатель: {order['recipient']}",
            f"Телефон: {order['phone']}",
            f"Адрес: {', '.join(address_parts)}",
        ]
        if order.get("zone_name"):
            details.append(f"Зона: {order['zone_name']}")
        for field, label in (
            ("floor", "Этаж"),
            ("apartment", "Квартира"),
            ("intercom", "Домофон"),
            ("comment", "Комментарий"),
        ):
            value = order.get(field)
            if value:
                details.append(f"{label}: {value}")
        text = "\n".join(details)
        if len(text) > MESSAGE_LIMIT:
            text = text[: MESSAGE_LIMIT - 1] + "…"
        return text

    def _send_order_message(
        self, chat_id: int | str, order: Mapping[str, object]
    ) -> None:
        self._api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": self._format_order(order),
                "reply_markup": self._order_keyboard(order),
            },
        )

    @staticmethod
    def _order_keyboard(order: Mapping[str, object]) -> dict[str, object]:
        status = order.get("status")
        if status == "Взят курьером":
            button_text = "Доставляется"
            action = "in_transit"
        elif status == "Доставляется":
            button_text = "Доставлен"
            action = "delivered"
        else:
            return {"inline_keyboard": []}

        return {
            "inline_keyboard": [
                [
                    {
                        "text": button_text,
                        "callback_data": f"order:{order['id']}:{action}",
                    }
                ]
            ]
        }

    def _handle_callback_query(self, callback: Mapping[str, object]) -> None:
        callback_id = callback.get("id")
        sender = callback.get("from")
        message = callback.get("message")
        data = callback.get("data")
        if (
            callback_id is None
            or not isinstance(sender, Mapping)
            or not isinstance(message, Mapping)
            or not isinstance(data, str)
        ):
            return

        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, Mapping) else None
        message_id = message.get("message_id")
        user_id = sender.get("id")
        match = re.fullmatch(
            r"order:([1-9][0-9]{0,17}):(in_transit|delivered)",
            data,
        )
        if chat_id is None or message_id is None or user_id is None or match is None:
            self._api_call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": "Кнопка больше не доступна."},
            )
            return

        order_id = int(match.group(1))
        target_status = (
            "Доставляется" if match.group(2) == "in_transit" else "Доставлен"
        )
        try:
            result = self.order_store.set_courier_order_status(
                order_id,
                chat_id,
                user_id,
                target_status,
            )
        except ValueError as error:
            self._api_call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": str(error)[:180]},
            )
            return
        except sqlite3.Error:
            self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Не удалось обновить заказ. Попробуйте позже.",
                },
            )
            return

        order = result["order"]
        if result["changed"] and self.on_order_updated is not None:
            self.on_order_updated()
        desired_text = self._format_order(order)
        desired_markup = self._order_keyboard(order)
        current_markup = message.get("reply_markup")
        if not isinstance(current_markup, Mapping):
            current_markup = {}
        self._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": "Статус заказа обновлён."
                if result["changed"]
                else "Заказ уже обновлён.",
            },
        )
        if (
            message.get("text") != desired_text
            or dict(current_markup) != desired_markup
        ):
            self._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": desired_text,
                    "reply_markup": desired_markup,
                },
            )

    def _send_message(
        self,
        chat_id: int | str,
        text: str,
        show_orders_button: bool = False,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if show_orders_button:
            payload["reply_markup"] = {
                "keyboard": [[{"text": ORDERS_BUTTON}]],
                "resize_keyboard": True,
            }
        elif reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._api_call("sendMessage", payload)
