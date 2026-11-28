import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse
import threading

from Module.order_storage import OrderStore

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "WebApp"


def verify_init_data(init_data: str, bot_token: str) -> dict:
    """Проверяет подпись Telegram Mini App и возвращает пользователя."""
    if not init_data or not bot_token:
        raise ValueError("Откройте приложение через Telegram.")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise ValueError("Не найдена подпись Telegram.")
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Не удалось подтвердить аккаунт Telegram.")
    auth_date = int(values.get("auth_date", "0"))
    if not auth_date or time.time() - auth_date > 86400:
        raise ValueError("Сессия устарела. Закройте приложение и откройте снова.")
    user = json.loads(values.get("user", "{}"))
    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError("Telegram не передал данные пользователя.")
    return user


class MiniAppServer:
    """Локальный API и статическая страница Telegram Mini App."""

    def __init__(self, order_store: OrderStore, bot_token: str):
        self.order_store = order_store
        self.bot_token = bot_token
        self.florist_ids = {
            value.strip()
            for value in os.getenv("FLORIST_TELEGRAM_IDS", "").split(",")
            if value.strip()
        }
        self.server = None
        self.thread = None

    def _role(self, user):
        user_id = str(user["id"])
        if user_id in self.florist_ids:
            return "florist"
        if self.order_store.get_courier_by_telegram_chat_id(user_id):
            return "courier"
        raise PermissionError(
            "Аккаунт не подключён. Попросите администратора выдать доступ."
        )

    def start(self):
        if self.server:
            return
        store = self.order_store
        token = self.bot_token
        florist_ids = self.florist_ids
        web_dir = WEBAPP_DIR

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def send_json(self, status, data):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def user_and_role(self):
                init_data = self.headers.get("X-Telegram-Init-Data", "")
                user = verify_init_data(init_data, token)
                user_id = str(user["id"])
                if user_id in florist_ids:
                    return user, "florist"
                if store.get_courier_by_telegram_chat_id(user_id):
                    return user, "courier"
                raise PermissionError("Аккаунт не подключён к TRAVA.")

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    try:
                        body = (web_dir / "index.html").read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                try:
                    user, role = self.user_and_role()
                    if path == "/api/me":
                        self.send_json(200, {
                            "role": role,
                            "name": user.get("first_name", "Сотрудник"),
                        })
                    elif path == "/api/orders":
                        if role == "florist":
                            from datetime import date
                            orders = store.list_orders(date.today())
                        else:
                            orders = store.list_current_orders_for_telegram(
                                str(user["id"])
                            )
                        self.send_json(200, {"orders": orders})
                    elif path == "/api/zones":
                        self.send_json(200, {"zones": store.list_delivery_zones()})
                    else:
                        self.send_error(404)
                except PermissionError as error:
                    self.send_json(403, {"error": str(error)})
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self.send_json(401, {"error": str(error)})

            def do_POST(self):
                if urlparse(self.path).path != "/api/orders":
                    self.send_error(404)
                    return
                try:
                    _user, role = self.user_and_role()
                    if role != "florist":
                        self.send_json(403, {"error": "Создавать заказы может только флорист."})
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 20000:
                        raise ValueError("Некорректный размер запроса.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Некорректные данные заказа.")
                    from datetime import date
                    delivery_date = date.fromisoformat(str(payload.get("order_date", "")))
                    zone_value = payload.get("zone_id")
                    zone_id = int(zone_value) if zone_value else None
                    order_id = store.create_order(
                        recipient=str(payload.get("recipient", "")),
                        phone=str(payload.get("phone", "")),
                        street_address=str(payload.get("street_address", "")),
                        floor=str(payload.get("floor", "")),
                        apartment=str(payload.get("apartment", "")),
                        intercom=str(payload.get("intercom", "")),
                        order_date=delivery_date,
                        delivery_time=str(payload.get("delivery_time", "")),
                        comment=str(payload.get("comment", "")),
                        zone_id=zone_id,
                        zone_source="florist_mini_app",
                        zone_detection_status="manual" if zone_id else "unavailable",
                    )
                    self.send_json(201, {
                        "success": True,
                        "order": store.get_order(order_id),
                    })
                except PermissionError as error:
                    self.send_json(403, {"error": str(error)})
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self.send_json(400, {"error": str(error)})
                except Exception:
                    self.send_json(500, {"error": "Не удалось сохранить заказ."})

            def do_PATCH(self):
                parts = urlparse(self.path).path.strip("/").split("/")
                if len(parts) != 4 or parts[:2] != ["api", "orders"] or parts[3] != "status":
                    self.send_error(404)
                    return
                try:
                    user, role = self.user_and_role()
                    if role != "courier":
                        self.send_json(403, {"error": "Изменять статус может только курьер."})
                        return
                    order_id = int(parts[2])
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 2000:
                        raise ValueError("Некорректный запрос.")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    status = payload.get("status")
                    result = store.set_courier_order_status(
                        order_id, str(user["id"]), str(user["id"]), status
                    )
                    self.send_json(200, {
                        "success": True,
                        "order": result["order"],
                    })
                except PermissionError as error:
                    self.send_json(403, {"error": str(error)})
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self.send_json(400, {"error": str(error)})
                except Exception:
                    self.send_json(500, {"error": "Не удалось изменить статус."})

        self.server = ThreadingHTTPServer(("0.0.0.0", int(os.getenv("MINI_APP_PORT", "8765"))), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True, name="TRAVA-MiniApp"
        )
        self.thread.start()

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread = None
