from collections.abc import Iterable, Mapping
from dataclasses import dataclass

ORDER_COLUMN_HEADERS = (
    "Номер заказа",
    "Получатель",
    "Номер телефона",
    "Адрес",
    "Зона",
    "Время доставки",
    "Статус доставки",
    "Комментарий",
    "Курьер",
)

EMPTY_VALUE = "—"
ADDRESS_PREFIX = "Кировская область, Киров"


@dataclass(frozen=True)
class OrderTableRow:
    order_id: str | None
    values: tuple[str, ...]


def _display_value(value: object, fallback: str = EMPTY_VALUE) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _order_sort_key(order: Mapping[str, object]) -> tuple[bool, str, int]:
    delivery_time = _display_value(order.get("delivery_time"), "")
    order_id = order.get("id")
    try:
        numeric_id = int(order_id) if order_id is not None else 0
    except (TypeError, ValueError):
        numeric_id = 0
    return not bool(delivery_time), delivery_time, numeric_id


def format_address(order: Mapping[str, object]) -> str:
    street_address = _display_value(order.get("street_address"), "")
    if not street_address:
        return EMPTY_VALUE
    return f"{ADDRESS_PREFIX}, {street_address}"


def _prepare_row(order: Mapping[str, object]) -> OrderTableRow:
    order_id = _display_value(order.get("id"), "")
    return OrderTableRow(
        order_id=order_id or None,
        values=(
            _display_value(order.get("order_no")),
            _display_value(order.get("recipient")),
            _display_value(order.get("phone")),
            format_address(order),
            _display_value(order.get("zone_name")),
            _display_value(order.get("delivery_time")),
            _display_value(order.get("status"), "Не указан"),
            _display_value(order.get("comment")),
            _display_value(order.get("courier_name")),
        ),
    )


def prepare_orders(orders: Iterable[object]) -> list[OrderTableRow]:
    visible_orders = [
        order
        for order in orders
        if isinstance(order, Mapping)
    ]
    visible_orders.sort(key=_order_sort_key)
    return [_prepare_row(order) for order in visible_orders]
