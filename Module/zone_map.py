import json
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from Module.order_storage import OrderStore

MAP_PAGE = Path(__file__).with_name("zones_map.html")
MAX_REQUEST_SIZE = 1_000_000


class ZoneMapServer:
    def __init__(self, order_store: OrderStore):
        self.order_store = order_store
        self.token = secrets.token_urlsafe(24)
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self._make_handler(),
        )
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="delivery-zone-map",
            daemon=True,
        )
        self.thread.start()
        self.url = (
            f"http://127.0.0.1:{self.httpd.server_port}/map/{self.token}"
            f"?token={self.token}"
        )

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _make_handler(self):
        store = self.order_store
        token = self.token

        class ZoneMapRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                request = urlparse(self.path)
                if request.path == f"/map/{token}":
                    try:
                        body = MAP_PAGE.read_bytes()
                    except OSError:
                        self._send_json(500, {"error": "Не удалось открыть карту зон."})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if request.path != "/api/zones" or not self._authorized(request):
                    self._send_json(404, {"error": "Страница не найдена."})
                    return
                try:
                    self._send_json(200, {"zones": store.list_delivery_zones()})
                except sqlite3.Error:
                    self._send_json(500, {"error": "Не удалось загрузить зоны из БД."})

            def do_PUT(self):
                request = urlparse(self.path)
                if request.path != "/api/zones" or not self._authorized(request):
                    self._send_json(404, {"error": "Страница не найдена."})
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_json(400, {"error": "Некорректный размер запроса."})
                    return
                if not 0 < content_length <= MAX_REQUEST_SIZE:
                    self._send_json(413, {"error": "Размер данных зон превышает лимит."})
                    return
                try:
                    payload = json.loads(self.rfile.read(content_length))
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("zones"), list
                    ):
                        raise ValueError("Ожидался список зон.")
                    zones = store.replace_delivery_zones(payload["zones"])
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
                    self._send_json(400, {"error": str(error)})
                    return
                except sqlite3.Error:
                    self._send_json(500, {"error": "Не удалось сохранить зоны в БД."})
                    return
                self._send_json(200, {"zones": zones})

            def _authorized(self, request) -> bool:
                return parse_qs(request.query).get("token") == [token]

            def _send_json(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args) -> None:
                return

        return ZoneMapRequestHandler
