from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone

ORDER_COLUMN_HEADERS = (
    "Номер заказа",
    "Получатель",
    "Номер телефона",
    "Адрес",
    "Время доставки",
    "Статус доставки",
    "Курьер",
)

EMPTY_VALUE = "—"


@dataclass(frozen=True)
class OrderTableRow:
    order_id: str | None
    values: tuple[str, ...]


def _order_attributes(order: Mapping[str, object]) -> Mapping[str, object]:
    attributes = order.get("attributes")
    if isinstance(attributes, Mapping):
        return attributes
    return order


def _display_value(value: object, fallback: str = EMPTY_VALUE) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _order_sort_time(order: Mapping[str, object]) -> datetime | None:
    attributes = _order_attributes(order)
    parsed = _parse_datetime(attributes.get("deliveryTimeFrom"))
    if parsed is None:
        parsed = _parse_datetime(attributes.get("dueTime"))
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _order_sort_key(order: Mapping[str, object]) -> tuple[bool, datetime]:
    delivery_time = _order_sort_time(order)
    return delivery_time is None, delivery_time or datetime.max


def format_address(order: Mapping[str, object]) -> str:
    attributes = _order_attributes(order)
    parts = [
        _display_value(attributes.get("deliveryCity"), ""),
        _display_value(attributes.get("deliveryStreet"), ""),
    ]

    for field, prefix in (
        ("deliveryHouse", "д. "),
        ("deliveryBuilding", "корп. "),
        ("deliveryApartment", "кв. "),
    ):
        value = _display_value(attributes.get(field), "")
        if value:
            parts.append(f"{prefix}{value}")

    address = ", ".join(part for part in parts if part)
    return address or EMPTY_VALUE


def _format_delivery_time(order: Mapping[str, object]) -> str:
    attributes = _order_attributes(order)
    start_value = attributes.get("deliveryTimeFrom")
    end_value = attributes.get("deliveryTimeTo")

    def display_time(value: object) -> str | None:
        if value is None:
            return None
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed.strftime("%H:%M")
        text = _display_value(value, "")
        return text or None

    start = display_time(start_value)
    end = display_time(end_value)
    if start and end:
        return f"{start}–{end}"
    return start or end or EMPTY_VALUE


def _order_id(order: Mapping[str, object]) -> str | None:
    value = order.get("id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _prepare_row(
    order: Mapping[str, object],
    couriers_by_order_id: Mapping[str, str],
) -> OrderTableRow:
    attributes = _order_attributes(order)
    order_id = _order_id(order)
    courier = (
        couriers_by_order_id.get(order_id, EMPTY_VALUE)
        if order_id is not None
        else EMPTY_VALUE
    )
    status = _display_value(attributes.get("deliveryStatus"), "Не указан")

    return OrderTableRow(
        order_id=order_id,
        values=(
            _display_value(attributes.get("docNo")),
            _display_value(attributes.get("deliveryContact")),
            _display_value(attributes.get("deliveryPhoneNumber")),
            format_address(order),
            _format_delivery_time(order),
            status,
            _display_value(courier),
        ),
    )


def prepare_orders(
    orders: Iterable[object],
    couriers_by_order_id: Mapping[str, str] | None = None,
) -> list[OrderTableRow]:
    courier_assignments = couriers_by_order_id or {}
    visible_orders = [
        order
        for order in orders
        if isinstance(order, Mapping)
        and _order_attributes(order).get("deliveryStatus") != "delivered"
    ]
    visible_orders.sort(key=_order_sort_key)
    return [_prepare_row(order, courier_assignments) for order in visible_orders]
