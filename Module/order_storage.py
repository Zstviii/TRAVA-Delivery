import hashlib
import json
import math
import secrets
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from Module.zone_geometry import point_in_polygon, validate_polygon

DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "orders.sqlite3"


class OrderStore:
    def __init__(self, database_path: str | Path = DATABASE_PATH):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS couriers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE,
                    telegram_chat_id TEXT,
                    telegram_user_id TEXT,
                    telegram_username TEXT,
                    link_code_hash TEXT,
                    link_code_created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS delivery_zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    geometry TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS orders (
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
                    courier_id INTEGER REFERENCES couriers(id) ON DELETE SET NULL,
                    zone_id INTEGER REFERENCES delivery_zones(id) ON DELETE SET NULL,
                    zone_source TEXT,
                    zone_detection_status TEXT NOT NULL DEFAULT 'unavailable',
                    latitude REAL,
                    longitude REAL,
                    delivered_at TEXT,
                    delivery_fee_kopecks INTEGER,
                    is_paid INTEGER NOT NULL DEFAULT 0,
                    paid_at TEXT
                );

                CREATE TABLE IF NOT EXISTS telegram_link_attempts (
                    chat_id TEXT PRIMARY KEY,
                    failed_attempts INTEGER NOT NULL,
                    window_started_at TEXT NOT NULL,
                    blocked_until TEXT
                );
                """
            )
            order_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(orders)")
            }
            for name, declaration in (
                ("zone_id", "INTEGER REFERENCES delivery_zones(id) ON DELETE SET NULL"),
                ("zone_source", "TEXT"),
                (
                    "zone_detection_status",
                    "TEXT NOT NULL DEFAULT 'unavailable'",
                ),
                ("latitude", "REAL"),
                ("longitude", "REAL"),
                ("delivered_at", "TEXT"),
                ("delivery_fee_kopecks", "INTEGER"),
                ("is_paid", "INTEGER NOT NULL DEFAULT 0"),
                ("paid_at", "TEXT"),
            ):
                if name not in order_columns:
                    connection.execute(
                        f"ALTER TABLE orders ADD COLUMN {name} {declaration}"
                    )

            courier_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(couriers)")
            }
            for name, declaration in (
                ("telegram_chat_id", "TEXT"),
                ("telegram_user_id", "TEXT"),
                ("telegram_username", "TEXT"),
                ("link_code_hash", "TEXT"),
                ("link_code_created_at", "TEXT"),
            ):
                if name not in courier_columns:
                    connection.execute(
                        f"ALTER TABLE couriers ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_couriers_telegram_chat_id
                ON couriers (telegram_chat_id)
                WHERE telegram_chat_id IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_couriers_link_code_hash
                ON couriers (link_code_hash)
                WHERE link_code_hash IS NOT NULL
                """
            )
            connection.execute(
                """
                UPDATE orders
                SET status = 'Взят курьером'
                WHERE courier_id IS NOT NULL AND status IN ('Новый', 'Назначен')
                """
            )

    def create_order(
        self,
        *,
        recipient: str,
        phone: str,
        street_address: str,
        floor: str = "",
        apartment: str = "",
        intercom: str = "",
        order_date: date | str,
        delivery_time: str = "",
        comment: str = "",
        zone_id: int | None = None,
        zone_source: str | None = None,
        zone_detection_status: str = "unavailable",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> int:
        recipient = recipient.strip()
        phone = phone.strip()
        street_address = street_address.strip()
        if not recipient or not phone or not street_address:
            raise ValueError("Заполните имя получателя, телефон и адрес.")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if zone_id is not None and connection.execute(
                "SELECT 1 FROM delivery_zones WHERE id = ? AND is_active = 1",
                (zone_id,),
            ).fetchone() is None:
                raise ValueError("Зона не найдена или уже не активна.")
            cursor = connection.execute(
                """
                INSERT INTO orders (
                    recipient, phone, street_address, floor, apartment, intercom,
                    order_date, delivery_time, comment, zone_id, zone_source,
                    zone_detection_status, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipient,
                    phone,
                    street_address,
                    floor.strip(),
                    apartment.strip(),
                    intercom.strip(),
                    order_date.isoformat() if isinstance(order_date, date) else order_date,
                    delivery_time.strip(),
                    comment.strip(),
                    zone_id,
                    zone_source,
                    zone_detection_status,
                    latitude,
                    longitude,
                ),
            )
            order_id = cursor.lastrowid
            connection.execute(
                "UPDATE orders SET order_no = ? WHERE id = ?",
                (f"ORD-{order_id:06d}", order_id),
            )
        return order_id

    def list_orders(self, order_date: date | str) -> list[dict[str, object]]:
        requested_date = (
            order_date.isoformat() if isinstance(order_date, date) else order_date
        )
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT o.id, o.order_no, o.recipient, o.phone, o.street_address,
                       o.floor, o.apartment, o.intercom, o.order_date,
                       o.delivery_time, o.status, o.comment, o.zone_id,
                       o.zone_source, o.zone_detection_status, o.latitude,
                       o.longitude, c.name AS courier_name,
                       c.phone AS courier_phone, z.name AS zone_name
                FROM orders AS o
                LEFT JOIN couriers AS c ON c.id = o.courier_id
                LEFT JOIN delivery_zones AS z ON z.id = o.zone_id
                WHERE o.order_date = ?
                ORDER BY CASE WHEN o.delivery_time = '' THEN 1 ELSE 0 END,
                         o.delivery_time, o.id
                """,
                (requested_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_order(self, order_id: int) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT o.*, c.name AS courier_name, c.phone AS courier_phone,
                       z.name AS zone_name
                FROM orders AS o
                LEFT JOIN couriers AS c ON c.id = o.courier_id
                LEFT JOIN delivery_zones AS z ON z.id = o.zone_id
                WHERE o.id = ?
                """,
                (order_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_delivery_zones(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, geometry
                FROM delivery_zones
                WHERE is_active = 1
                ORDER BY name, id
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "geometry": json.loads(row["geometry"]),
            }
            for row in rows
        ]

    def replace_delivery_zones(
        self, zones: list[Mapping[str, object]]
    ) -> list[dict[str, object]]:
        prepared: list[tuple[int | None, str, str]] = []
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for zone in zones:
            if not isinstance(zone, Mapping):
                raise ValueError("Некорректная зона.")
            name = zone.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Укажите название каждой зоны.")
            normalized_name = name.strip().casefold()
            if normalized_name in seen_names:
                raise ValueError("Названия зон должны быть уникальными.")
            seen_names.add(normalized_name)

            zone_id = zone.get("id")
            if zone_id is not None:
                if isinstance(zone_id, bool) or not isinstance(zone_id, int) or zone_id <= 0:
                    raise ValueError("Некорректный ID зоны.")
                if zone_id in seen_ids:
                    raise ValueError("ID зоны повторяется.")
                seen_ids.add(zone_id)

            geometry = validate_polygon(zone.get("geometry"))
            prepared.append(
                (
                    zone_id,
                    name.strip(),
                    json.dumps(geometry, ensure_ascii=False, separators=(",", ":")),
                )
            )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM delivery_zones WHERE is_active = 1"
                )
            }
            if seen_ids - active_ids:
                raise ValueError("Одна из зон не найдена или уже удалена.")

            saved_ids = set()
            for zone_id, name, geometry_json in prepared:
                if zone_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO delivery_zones (name, geometry, is_active)
                        VALUES (?, ?, 1)
                        """,
                        (name, geometry_json),
                    )
                    zone_id = cursor.lastrowid
                else:
                    connection.execute(
                        """
                        UPDATE delivery_zones
                        SET name = ?, geometry = ?, is_active = 1
                        WHERE id = ?
                        """,
                        (name, geometry_json, zone_id),
                    )
                saved_ids.add(zone_id)

            for zone_id in active_ids - saved_ids:
                connection.execute(
                    "UPDATE delivery_zones SET is_active = 0 WHERE id = ?",
                    (zone_id,),
                )
        return self.list_delivery_zones()

    def detect_delivery_zone(
        self, longitude: float, latitude: float
    ) -> tuple[int | None, str]:
        try:
            if isinstance(longitude, bool) or isinstance(latitude, bool):
                raise ValueError
            longitude = float(longitude)
            latitude = float(latitude)
        except (TypeError, ValueError) as error:
            raise ValueError("Некорректные координаты заказа.") from error
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("Координаты заказа находятся вне допустимого диапазона.")

        zones = self.list_delivery_zones()
        matches: list[int] = []
        boundary = False
        for zone in zones:
            is_inside, is_boundary = point_in_polygon(
                longitude,
                latitude,
                zone["geometry"],
            )
            if is_inside:
                matches.append(int(zone["id"]))
            boundary = boundary or is_boundary
        if boundary or len(matches) > 1:
            return None, "ambiguous"
        if matches:
            return matches[0], "matched"
        return None, "outside"

    def add_courier(self, name: str, phone: str) -> int:
        name = name.strip()
        phone = phone.strip()
        if not name or not phone:
            raise ValueError("Заполните имя и номер телефона курьера.")

        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO couriers (name, phone) VALUES (?, ?)",
                    (name, phone),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise ValueError("Курьер с таким номером уже существует.") from error

    def list_couriers(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, phone, telegram_chat_id, telegram_username
                FROM couriers ORDER BY name, id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_telegram_link_code(self, courier_id: int) -> str:
        with self._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM couriers WHERE id = ?", (courier_id,)
            ).fetchone() is None:
                raise LookupError("Курьер не найден.")
            for _ in range(10):
                code = f"{secrets.randbelow(1_000_000):06d}"
                code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
                try:
                    connection.execute(
                        """
                        UPDATE couriers
                        SET link_code_hash = ?, link_code_created_at = ?
                        WHERE id = ?
                        """,
                        (
                            code_hash,
                            datetime.now(UTC).isoformat(),
                            courier_id,
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RuntimeError("Не удалось создать уникальный код подключения.")
        return code

    def link_telegram_account(
        self,
        code: str,
        chat_id: int | str,
        telegram_user_id: int | str,
        username: str | None,
    ) -> None:
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        try:
            with self._connection() as connection:
                courier = connection.execute(
                    """
                    SELECT id, link_code_created_at
                    FROM couriers WHERE link_code_hash = ?
                    """,
                    (code_hash,),
                ).fetchone()
                if courier is None:
                    raise ValueError("Код не найден или уже использован.")

                created_at = datetime.fromisoformat(courier["link_code_created_at"])
                if now - created_at > timedelta(minutes=15):
                    raise ValueError("Срок действия кода истёк. Запросите новый код.")

                connection.execute(
                    """
                    UPDATE couriers
                    SET telegram_chat_id = ?, telegram_user_id = ?,
                        telegram_username = ?, link_code_hash = NULL,
                        link_code_created_at = NULL
                    WHERE id = ?
                    """,
                    (str(chat_id), str(telegram_user_id), username, courier["id"]),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "Этот Telegram-аккаунт уже привязан к другому курьеру."
            ) from error

    def get_courier_by_telegram_chat_id(
        self, chat_id: int | str
    ) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, name, phone, telegram_user_id, telegram_username
                FROM couriers WHERE telegram_chat_id = ?
                """,
                (str(chat_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def telegram_link_lockout_remaining(self, chat_id: int | str) -> int | None:
        now = datetime.now(UTC)
        chat_key = str(chat_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT window_started_at, blocked_until
                FROM telegram_link_attempts WHERE chat_id = ?
                """,
                (chat_key,),
            ).fetchone()
            if row is None:
                return None

            window_started_at = datetime.fromisoformat(row["window_started_at"])
            if now - window_started_at >= timedelta(minutes=15):
                connection.execute(
                    "DELETE FROM telegram_link_attempts WHERE chat_id = ?",
                    (chat_key,),
                )
                return None

            if row["blocked_until"] is None:
                return None
            blocked_until = datetime.fromisoformat(row["blocked_until"])
            remaining = (blocked_until - now).total_seconds()
            if remaining <= 0:
                connection.execute(
                    "DELETE FROM telegram_link_attempts WHERE chat_id = ?",
                    (chat_key,),
                )
                return None
            return math.ceil(remaining / 60)

    def record_telegram_link_failure(
        self, chat_id: int | str, limit: int = 5
    ) -> tuple[int, int | None]:
        now = datetime.now(UTC)
        chat_key = str(chat_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT failed_attempts, window_started_at
                FROM telegram_link_attempts WHERE chat_id = ?
                """,
                (chat_key,),
            ).fetchone()

            if row is None or now - datetime.fromisoformat(
                row["window_started_at"]
            ) >= timedelta(minutes=15):
                attempts = 1
                window_started_at = now
            else:
                attempts = row["failed_attempts"] + 1
                window_started_at = datetime.fromisoformat(row["window_started_at"])

            blocked_until = now + timedelta(minutes=15) if attempts >= limit else None
            connection.execute(
                """
                INSERT OR REPLACE INTO telegram_link_attempts (
                    chat_id, failed_attempts, window_started_at, blocked_until
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    chat_key,
                    attempts,
                    window_started_at.isoformat(),
                    blocked_until.isoformat() if blocked_until else None,
                ),
            )
            if blocked_until is None:
                return attempts, None
            return attempts, math.ceil((blocked_until - now).total_seconds() / 60)

    def reset_telegram_link_attempts(self, chat_id: int | str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM telegram_link_attempts WHERE chat_id = ?",
                (str(chat_id),),
            )

    def list_current_orders_for_telegram(
        self,
        chat_id: int | str,
        today: date | None = None,
    ) -> list[dict[str, object]]:
        requested_date = (today or date.today()).isoformat()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT o.id, o.order_no, o.recipient, o.phone, o.street_address,
                       o.floor, o.apartment, o.intercom, o.order_date,
                       o.delivery_time, o.comment, o.status, z.name AS zone_name
                FROM orders AS o
                JOIN couriers AS c ON c.id = o.courier_id
                LEFT JOIN delivery_zones AS z ON z.id = o.zone_id
                WHERE c.telegram_chat_id = ?
                  AND o.order_date >= ?
                  AND lower(o.status) NOT IN (
                      'delivered', 'доставлен', 'доставлено',
                      'cancelled', 'canceled', 'отменен', 'отменён'
                  )
                ORDER BY o.order_date, o.delivery_time, o.id
                """,
                (str(chat_id), requested_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def assign_courier(self, order_id: int, courier_phone: str | None) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                "SELECT courier_id, status FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            if order is None:
                raise LookupError("Заказ не найден.")

            courier_id = None
            if courier_phone is not None:
                courier = connection.execute(
                    "SELECT id FROM couriers WHERE phone = ?",
                    (courier_phone,),
                ).fetchone()
                if courier is None:
                    raise ValueError("Курьер с таким номером не найден.")
                courier_id = courier["id"]

            if order["courier_id"] == courier_id:
                return
            if order["status"] == "Доставлен":
                raise ValueError("Нельзя изменить курьера у доставленного заказа.")

            connection.execute(
                """
                UPDATE orders
                SET courier_id = ?, status = ?
                WHERE id = ?
                """,
                (
                    courier_id,
                    "Взят курьером" if courier_id is not None else "Новый",
                    order_id,
                ),
            )

    def update_order_assignment(
        self,
        order_id: int,
        courier_phone: str | None,
        zone_id: int | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                """
                SELECT courier_id, zone_id, status, zone_source,
                       zone_detection_status
                FROM orders WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
            if order is None:
                raise LookupError("Заказ не найден.")

            courier_id = None
            if courier_phone is not None:
                courier = connection.execute(
                    "SELECT id FROM couriers WHERE phone = ?",
                    (courier_phone,),
                ).fetchone()
                if courier is None:
                    raise ValueError("Курьер с таким номером не найден.")
                courier_id = courier["id"]

            courier_changed = order["courier_id"] != courier_id
            zone_changed = order["zone_id"] != zone_id
            if zone_changed and zone_id is not None and connection.execute(
                "SELECT 1 FROM delivery_zones WHERE id = ? AND is_active = 1",
                (zone_id,),
            ).fetchone() is None:
                raise ValueError("Зона не найдена или уже не активна.")
            if order["status"] == "Доставлен" and courier_changed:
                raise ValueError("Нельзя изменить курьера у доставленного заказа.")
            if not courier_changed and not zone_changed:
                return

            status = order["status"]
            if courier_changed:
                status = "Взят курьером" if courier_id is not None else "Новый"
            connection.execute(
                """
                UPDATE orders
                SET courier_id = ?, zone_id = ?, zone_source = ?,
                    zone_detection_status = ?, status = ?
                WHERE id = ?
                """,
                (
                    courier_id,
                    zone_id,
                    ("manual" if zone_id is not None else None)
                    if zone_changed
                    else order["zone_source"],
                    ("manual" if zone_id is not None else "unassigned")
                    if zone_changed
                    else order["zone_detection_status"],
                    status,
                    order_id,
                ),
            )

    def set_order_zone(self, order_id: int, zone_id: int | None) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if zone_id is not None and connection.execute(
                "SELECT 1 FROM delivery_zones WHERE id = ? AND is_active = 1",
                (zone_id,),
            ).fetchone() is None:
                raise ValueError("Зона не найдена или уже не активна.")
            cursor = connection.execute(
                """
                UPDATE orders
                SET zone_id = ?, zone_source = ?, zone_detection_status = ?
                WHERE id = ?
                """,
                (
                    zone_id,
                    "manual" if zone_id is not None else None,
                    "manual" if zone_id is not None else "unassigned",
                    order_id,
                ),
            )
            if cursor.rowcount == 0:
                raise LookupError("Заказ не найден.")

    def set_courier_order_status(
        self,
        order_id: int,
        chat_id: int | str,
        telegram_user_id: int | str,
        status: str,
    ) -> dict[str, object]:
        if status not in {"Доставляется", "Доставлен"}:
            raise ValueError("Курьеру недоступен выбранный статус.")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT o.*, c.id AS assigned_courier_id,
                       c.telegram_chat_id, c.telegram_user_id,
                       c.name AS courier_name, c.phone AS courier_phone,
                       z.name AS zone_name
                FROM orders AS o
                JOIN couriers AS c ON c.id = o.courier_id
                LEFT JOIN delivery_zones AS z ON z.id = o.zone_id
                WHERE o.id = ?
                """,
                (order_id,),
            ).fetchone()
            if (
                row is None
                or row["telegram_chat_id"] != str(chat_id)
                or row["telegram_user_id"] != str(telegram_user_id)
            ):
                raise ValueError("Этот заказ не назначен вашему аккаунту.")

            current_status = row["status"]
            if current_status == status:
                return {"changed": False, "order": dict(row)}
            if current_status == "Доставлен" and status == "Доставляется":
                return {"changed": False, "order": dict(row)}

            allowed_next_status = {
                "Взят курьером": "Доставляется",
                "Доставляется": "Доставлен",
            }.get(current_status)
            if allowed_next_status != status:
                raise ValueError("Заказ больше не доступен для изменения.")

            delivered_at = (
                datetime.now().astimezone().isoformat()
                if status == "Доставлен"
                else row["delivered_at"]
            )
            connection.execute(
                "UPDATE orders SET status = ?, delivered_at = ? WHERE id = ?",
                (status, delivered_at, order_id),
            )
            updated = connection.execute(
                """
                SELECT o.*, c.name AS courier_name, c.phone AS courier_phone,
                       z.name AS zone_name
                FROM orders AS o
                LEFT JOIN couriers AS c ON c.id = o.courier_id
                LEFT JOIN delivery_zones AS z ON z.id = o.zone_id
                WHERE o.id = ?
                """,
                (order_id,),
            ).fetchone()
        return {"changed": True, "order": dict(updated)}

    def list_courier_delivery_history(
        self,
        courier_id: int,
        delivery_date: date | str,
    ) -> list[dict[str, object]]:
        requested_date = (
            delivery_date.isoformat()
            if isinstance(delivery_date, date)
            else delivery_date
        )
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, order_no, recipient, phone, street_address,
                       order_date, delivery_time, comment, delivered_at,
                       delivery_fee_kopecks, is_paid, paid_at
                FROM orders
                WHERE courier_id = ?
                  AND status = 'Доставлен'
                  AND COALESCE(substr(delivered_at, 1, 10), order_date) = ?
                ORDER BY delivered_at, delivery_time, id
                """,
                (courier_id, requested_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_courier_delivery_amounts(
        self,
        courier_id: int,
        delivery_date: date | str,
        amounts: Mapping[int, int | None],
    ) -> None:
        requested_date = (
            delivery_date.isoformat()
            if isinstance(delivery_date, date)
            else delivery_date
        )
        for amount in amounts.values():
            if amount is not None and (
                isinstance(amount, bool)
                or not isinstance(amount, int)
                or not 0 <= amount <= 999_999_999
            ):
                raise ValueError("Сумма оплаты должна быть от 0 до 9 999 999,99 ₽.")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id, is_paid
                FROM orders
                WHERE courier_id = ?
                  AND status = 'Доставлен'
                  AND COALESCE(substr(delivered_at, 1, 10), order_date) = ?
                """,
                (courier_id, requested_date),
            ).fetchall()
            eligible = {row["id"]: row["is_paid"] for row in rows}
            if set(amounts) - eligible.keys():
                raise ValueError("Один из заказов уже не относится к этой истории.")
            if any(
                amount is None and eligible[order_id]
                for order_id, amount in amounts.items()
            ):
                raise ValueError("Нельзя удалить сумму уже оплаченного заказа.")

            connection.executemany(
                """
                UPDATE orders
                SET delivery_fee_kopecks = ?
                WHERE id = ? AND courier_id = ? AND status = 'Доставлен'
                  AND COALESCE(substr(delivered_at, 1, 10), order_date) = ?
                """,
                (
                    (amount, order_id, courier_id, requested_date)
                    for order_id, amount in amounts.items()
                ),
            )

    def set_courier_delivery_paid(
        self,
        order_id: int,
        courier_id: int,
        is_paid: bool,
    ) -> None:
        if not isinstance(is_paid, bool):
            raise ValueError("Некорректный статус оплаты.")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                """
                SELECT delivery_fee_kopecks, is_paid
                FROM orders
                WHERE id = ? AND courier_id = ? AND status = 'Доставлен'
                """,
                (order_id, courier_id),
            ).fetchone()
            if order is None:
                raise LookupError("Доставленный заказ этого курьера не найден.")
            if is_paid and order["delivery_fee_kopecks"] is None:
                raise ValueError("Сначала укажите и сохраните сумму за заказ.")
            if bool(order["is_paid"]) == is_paid:
                return

            paid_at = datetime.now().astimezone().isoformat() if is_paid else None
            connection.execute(
                "UPDATE orders SET is_paid = ?, paid_at = ? WHERE id = ?",
                (int(is_paid), paid_at, order_id),
            )
