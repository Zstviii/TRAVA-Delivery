import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

import requests
from dotenv import load_dotenv

from Module.address_lookup import resolve_delivery_address
from Module.order_processing import ADDRESS_PREFIX
from Module.order_storage import OrderStore

load_dotenv(Path(__file__).resolve().parent.parent / "Config" / ".env")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
ORDERS_BUTTON = "Заказы"
CREATE_ORDER_BUTTON = "Создать заказ"
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
        self.florist_telegram_ids = {v.strip() for v in os.getenv('FLORIST_TELEGRAM_IDS', '').split(',') if v.strip().isdigit()}
        self.florist_sessions: dict[str, dict[str, object]] = {}

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

        if str(user_id) in self.florist_telegram_ids:
            if command == "/start":
                self._send_message(
                    chat_id,
                    "Здравствуйте! Здесь можно быстро создать заказ для Trava. Все заказы сохраняются в локальную базу магазина.",
                    reply_markup=self._florist_keyboard(),
                )
                return
            self._handle_florist_message(chat_id, text)
            return

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

    @staticmethod
    def _florist_keyboard() -> dict[str, object]:
        return {"keyboard": [[{"text": CREATE_ORDER_BUTTON}]], "resize_keyboard": True}

    def _handle_florist_message(self, chat_id: int | str, text: str) -> None:
        key = str(chat_id)
        value = text.strip()
        if value.casefold() in {"/cancel", "отмена", "отменить"}:
            self.florist_sessions.pop(key, None)
            self._send_message(chat_id, "Создание заказа отменено.", reply_markup=self._florist_keyboard())
            return
        if value.casefold() in {CREATE_ORDER_BUTTON.casefold(), "/neworder"}:
            self.florist_sessions[key] = {"step": "recipient", "data": {}}
            self._send_message(chat_id, "Введите имя получателя:")
            return
        session = self.florist_sessions.get(key)
        if session is None:
            self._send_message(chat_id, "Нажмите «Создать заказ», чтобы начать. Для отмены отправьте /cancel.", reply_markup=self._florist_keyboard())
            return

        step = str(session["step"])
        data = session["data"]
        prompts = {
            "recipient": ("recipient", "Введите номер телефона получателя:"),
            "phone": ("phone", "Введите адрес доставки:"),
            "address": ("street_address", "Введите дату доставки ГГГГ-ММ-ДД (или «-» для сегодня):"),
            "date": (
                "order_date",
                "Введите время ЧЧ:ММ или интервал ЧЧ:ММ–ЧЧ:ММ "
                "(«-», если время не определено):",
            ),
            "time": ("delivery_time", "Этаж (или «-»):"),
            "floor": ("floor", "Квартира (или «-»):"),
            "apartment": ("apartment", "Домофон (или «-»):"),
            "intercom": ("intercom", "Комментарий (или «-»):"),
            "comment": ("comment", None),
        }
        field, prompt = prompts[step]
        if step in {"recipient", "phone", "address"} and not value:
            self._send_message(chat_id, "Поле не должно быть пустым. Повторите ввод:")
            return
        if step == "date":
            if value == "-":
                value = date.today().isoformat()
            else:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    self._send_message(chat_id, "Неверная дата. Используйте ГГГГ-ММ-ДД или «-».")
                    return
        if step == "time":
            if value == "-":
                data["delivery_time"] = ""
                data["delivery_time_end"] = ""
            else:
                match = re.fullmatch(
                    r"(\d{2}:\d{2})(?:(?:\s*(?:-|–|—)\s*|\s+)(\d{2}:\d{2}))?",
                    value,
                )
                if match is None:
                    self._send_message(
                        chat_id,
                        "Неверное время. Используйте ЧЧ:ММ или интервал "
                        "ЧЧ:ММ–ЧЧ:ММ.",
                    )
                    return
                start_time, end_time = match.groups()
                try:
                    parsed_start = datetime.strptime(start_time, "%H:%M")
                    parsed_end = (
                        datetime.strptime(end_time, "%H:%M")
                        if end_time
                        else None
                    )
                except ValueError:
                    self._send_message(
                        chat_id,
                        "Неверное время. Используйте формат ЧЧ:ММ.",
                    )
                    return
                if parsed_end is not None and parsed_end < parsed_start:
                    self._send_message(
                        chat_id,
                        "Окончание доставки не может быть раньше начала.",
                    )
                    return
                data["delivery_time"] = start_time
                data["delivery_time_end"] = end_time or ""
        else:
            data[field] = "" if value == "-" else value
        next_steps = {"recipient": "phone", "phone": "address", "address": "date", "date": "time", "time": "floor", "floor": "apartment", "apartment": "intercom", "intercom": "comment"}
        if step != "comment":
            session["step"] = next_steps[step]
            self._send_message(chat_id, prompt)
            return
        self._prepare_florist_order(chat_id, session)

    def _prepare_florist_order(self, chat_id: int | str, session: dict[str, object]) -> None:
        data = session["data"]
        try:
            address_lookup = resolve_delivery_address(
                str(data["street_address"]),
                self.order_store.detect_delivery_zone,
            )
            if address_lookup.normalized_address:
                data["street_address"] = address_lookup.normalized_address
            data.update(
                {
                    "latitude": address_lookup.latitude,
                    "longitude": address_lookup.longitude,
                    "zone_id": address_lookup.zone_id,
                    "zone_source": (
                        "dadata" if address_lookup.zone_id is not None else None
                    ),
                    "zone_detection_status": (
                        address_lookup.zone_detection_status
                    ),
                }
            )
        except (RuntimeError, ValueError) as error:
            data["zone_detection_status"] = "unavailable"
            data["address_lookup_error"] = str(error)
        session["step"] = "confirm"
        delivery_time = str(data.get("delivery_time") or "")
        delivery_time_end = str(data.get("delivery_time_end") or "")
        if delivery_time and delivery_time_end:
            delivery_time = f"{delivery_time}–{delivery_time_end}"
        zone_details = {
            "outside": "Адрес вне настроенных зон. Попросите администратора "
            "назначить зону в приложении.",
            "ambiguous": "Адрес попал на границу или сразу в несколько зон. "
            "Попросите администратора выбрать зону в приложении.",
            "low_accuracy": "DaData не определила адрес с нужной точностью. "
            "Попросите администратора выбрать зону в приложении.",
            "unavailable": (
                f"Не удалось проверить адрес: "
                f"{data.get('address_lookup_error', 'DaData недоступна')}"
            ),
        }
        zone_status = str(data.get("zone_detection_status", "unavailable"))
        zone_summary = (
            "Определена автоматически."
            if data.get("zone_id") is not None
            else zone_details.get(
                zone_status,
                "Не определена. Попросите администратора назначить зону "
                "в приложении.",
            )
        )
        preview = "\n".join(
            (
                "Проверьте заказ перед сохранением:",
                "",
                f"Получатель: {data['recipient']}",
                f"Телефон: {data['phone']}",
                f"Адрес: {data['street_address']}",
                f"Дата: {data['order_date']}",
                f"Время доставки: {delivery_time or 'Не указано'}",
                f"Этаж: {data['floor'] or '—'}",
                f"Квартира: {data['apartment'] or '—'}",
                f"Домофон: {data['intercom'] or '—'}",
                f"Комментарий: {data['comment'] or '—'}",
                f"Зона: {zone_summary}",
            )
        )
        self._send_message(chat_id, preview, reply_markup={"inline_keyboard": [[{"text": "Сохранить заказ", "callback_data": "florist:create"}, {"text": "Отменить", "callback_data": "florist:cancel"}]]})

    def _handle_florist_callback(self, callback_id: object, chat_id: int | str, message_id: int, data: str) -> None:
        key = str(chat_id)
        session = self.florist_sessions.get(key)
        if session is None or session.get("step") != "confirm":
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Эта форма уже закрыта."})
            return
        if data == "florist:cancel":
            self.florist_sessions.pop(key, None)
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Создание отменено."})
            self._api_call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "Создание заказа отменено."})
            return
        if data != "florist:create":
            return
        order = session["data"]
        try:
            order_id = self.order_store.create_order(
                recipient=str(order["recipient"]),
                phone=str(order["phone"]),
                street_address=str(order["street_address"]),
                floor=str(order.get("floor", "")),
                apartment=str(order.get("apartment", "")),
                intercom=str(order.get("intercom", "")),
                order_date=str(order["order_date"]),
                delivery_time=str(order.get("delivery_time", "")),
                delivery_time_end=str(order.get("delivery_time_end", "")),
                comment=str(order.get("comment", "")),
                zone_id=order.get("zone_id"),
                zone_source=order.get("zone_source"),
                zone_detection_status=str(
                    order.get("zone_detection_status", "unavailable")
                ),
                latitude=order.get("latitude"),
                longitude=order.get("longitude"),
            )
        except (ValueError, sqlite3.Error):
            self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Не удалось сохранить заказ. Проверьте данные и повторите."})
            return
        self.florist_sessions.pop(key, None)
        saved = self.order_store.get_order(order_id)
        order_no = saved.get("order_no") if saved else f"ORD-{order_id:06d}"
        self._api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Заказ сохранён."})
        self._api_call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": f"Заказ {order_no} успешно сохранён в локальную базу Trava."})
        if self.on_order_updated is not None:
            self.on_order_updated()

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
            reply_markup=self._orders_keyboard(orders),
        )

    @staticmethod
    def _format_order(order: Mapping[str, object]) -> str:
        address_parts = [
            ADDRESS_PREFIX,
            str(order["street_address"]),
        ]
        delivery_time = str(order.get("delivery_time") or "")
        delivery_time_end = str(order.get("delivery_time_end") or "")
        if delivery_time and delivery_time_end:
            delivery_time = f"{delivery_time}–{delivery_time_end}"
        details = [
            f"Заказ {order['order_no']}",
            f"Дата и время: {order['order_date']} {delivery_time}".strip(),
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

    @staticmethod
    def _orders_keyboard(
        orders: list[Mapping[str, object]],
    ) -> dict[str, object]:
        buttons = []
        for order in orders:
            address = str(order.get("street_address") or "").strip()
            status = str(order.get("status") or "Статус не указан")
            suffix = f" · {status}"
            available_address_length = max(1, 63 - len(suffix))
            if len(address) > available_address_length:
                address = address[:available_address_length].rstrip() + "…"
            button_text = f"{address or 'Адрес не указан'}{suffix}"
            buttons.append(
                [
                    {
                        "text": button_text[:64],
                        "callback_data": f"order:{order['id']}:view",
                    }
                ]
            )
        return {"inline_keyboard": buttons}

    @staticmethod
    def _order_keyboard(order: Mapping[str, object]) -> dict[str, object]:
        rows = []
        action = {
            "Взят курьером": ("Доставляется", "in_transit"),
            "Доставляется": ("Доставлен", "delivered"),
        }.get(order.get("status"))
        if action is not None:
            button_text, action_name = action
            rows.append(
                [
                    {
                        "text": button_text,
                        "callback_data": f"order:{order['id']}:{action_name}",
                    }
                ]
            )
        rows.append([{"text": "← К заказам", "callback_data": "orders:list"}])
        return {"inline_keyboard": rows}

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
        if chat_id is None or message_id is None or user_id is None:
            return

        if str(user_id) in self.florist_telegram_ids and data.startswith("florist:"):
            self._handle_florist_callback(callback_id, chat_id, message_id, data)
            return

        if data == "orders:list":
            self._handle_orders_list_callback(
                callback_id,
                chat_id,
                message_id,
                user_id,
                message,
            )
            return

        view_match = re.fullmatch(r"order:([1-9][0-9]{0,17}):view", data)
        if view_match is not None:
            self._handle_order_view_callback(
                callback_id,
                chat_id,
                message_id,
                user_id,
                int(view_match.group(1)),
                message,
            )
            return

        match = re.fullmatch(
            r"order:([1-9][0-9]{0,17}):(in_transit|delivered)",
            data,
        )
        if match is None:
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

    def _handle_orders_list_callback(
        self,
        callback_id: object,
        chat_id: int | str,
        message_id: int,
        user_id: int | str,
        message: Mapping[str, object],
    ) -> None:
        try:
            courier = self.order_store.get_courier_by_telegram_chat_id(chat_id)
            if (
                courier is None
                or str(courier["telegram_user_id"]) != str(user_id)
            ):
                self._api_call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                        "text": "Аккаунт курьера не подключён.",
                    },
                )
                return

            orders = self.order_store.list_current_orders_for_telegram(chat_id)
        except sqlite3.Error:
            self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Не удалось загрузить заказы.",
                },
            )
            return

        text = (
            f"Актуальные заказы для {str(courier['name'])[:100]}:"
            if orders
            else "Сейчас у вас нет актуальных заказов."
        )
        reply_markup = self._orders_keyboard(orders)
        self._api_call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Список заказов"},
        )
        if (
            message.get("text") != text
            or message.get("reply_markup") != reply_markup
        ):
            self._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "reply_markup": reply_markup,
                },
            )

    def _handle_order_view_callback(
        self,
        callback_id: object,
        chat_id: int | str,
        message_id: int,
        user_id: int | str,
        order_id: int,
        message: Mapping[str, object],
    ) -> None:
        try:
            courier = self.order_store.get_courier_by_telegram_chat_id(chat_id)
            if (
                courier is None
                or str(courier["telegram_user_id"]) != str(user_id)
            ):
                order = None
            else:
                order = next(
                    (
                        item
                        for item in self.order_store.list_current_orders_for_telegram(
                            chat_id
                        )
                        if int(item["id"]) == order_id
                    ),
                    None,
                )
        except (TypeError, ValueError, sqlite3.Error):
            order = None

        if order is None:
            self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Заказ больше не назначен вашему аккаунту.",
                },
            )
            return

        text = self._format_order(order)
        reply_markup = self._order_keyboard(order)
        self._api_call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Карточка заказа"},
        )
        if (
            message.get("text") != text
            or message.get("reply_markup") != reply_markup
        ):
            self._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "reply_markup": reply_markup,
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
