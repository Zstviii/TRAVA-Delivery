import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from dadata import Dadata
from dotenv import load_dotenv

from Module.order_processing import ADDRESS_PREFIX
from Module.order_storage import OrderStore

load_dotenv(Path(__file__).resolve().parent.parent / "Config" / ".env")


@dataclass(frozen=True)
class DeliveryAddressLookup:
    normalized_address: str | None
    latitude: float | None
    longitude: float | None
    zone_id: int | None
    zone_detection_status: str


class DadataAPI:
    @staticmethod
    def check_address(address: str) -> dict[str, object]:
        if not address.strip():
            raise ValueError("Адрес не может быть пустым.")

        token = os.getenv("DADATA_API")
        if not token:
            raise RuntimeError("Не задан токен DaData (DADATA_API).")

        try:
            with Dadata(token, os.getenv("DADATA_SECRET")) as dadata:
                cleaned_address = dadata.clean("address", address)
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"DaData ответила HTTP {error.response.status_code}."
            ) from error
        except httpx.TimeoutException as error:
            raise RuntimeError("Истекло время ожидания ответа DaData.") from error
        except httpx.RequestError as error:
            raise RuntimeError(
                f"Сетевая ошибка DaData ({type(error).__name__})."
            ) from error
        except httpx.HTTPError as error:
            raise RuntimeError("Не удалось проверить адрес через DaData.") from error
        except ValueError as error:
            raise RuntimeError("DaData вернула некорректный ответ.") from error
        if cleaned_address is None:
            raise RuntimeError("DaData не вернул результат проверки адреса.")

        return {
            "address": cleaned_address.get("result"),
            "geo_lat": cleaned_address.get("geo_lat"),
            "geo_lon": cleaned_address.get("geo_lon"),
            "qc_geo": cleaned_address.get("qc_geo"),
        }


def resolve_delivery_address(
    address: str,
    detect_delivery_zone: Callable[[float, float], tuple[int | None, str]],
) -> DeliveryAddressLookup:
    checked = DadataAPI.check_address(f"{ADDRESS_PREFIX}, {address.strip()}")
    return _resolve_lookup_result(checked, detect_delivery_zone)


def _resolve_lookup_result(
    checked: dict[str, object],
    detect_delivery_zone: Callable[[float, float], tuple[int | None, str]],
) -> DeliveryAddressLookup:
    normalized_address = checked.get("address")
    if not isinstance(normalized_address, str):
        normalized_address = None

    try:
        latitude = float(checked["geo_lat"])
        longitude = float(checked["geo_lon"])
        accuracy = int(checked["qc_geo"])
    except (KeyError, TypeError, ValueError):
        return DeliveryAddressLookup(
            normalized_address, None, None, None, "low_accuracy"
        )
    if accuracy > 1:
        return DeliveryAddressLookup(
            normalized_address, None, None, None, "low_accuracy"
        )

    try:
        zone_id, status = detect_delivery_zone(longitude, latitude)
    except ValueError:
        return DeliveryAddressLookup(
            normalized_address, None, None, None, "low_accuracy"
        )
    return DeliveryAddressLookup(
        normalized_address,
        latitude,
        longitude,
        zone_id,
        status,
    )


def _run_address_diagnostic() -> int:
    address = input("Введите адрес (например, Калинина 9): ").strip()
    if not address:
        print("Адрес не указан.")
        return 2

    query = f"{ADDRESS_PREFIX}, {address}"
    try:
        checked = DadataAPI.check_address(query)
        store = OrderStore()
        result = _resolve_lookup_result(checked, store.detect_delivery_zone)
        zones = store.list_delivery_zones()
    except RuntimeError as error:
        print(f"Ошибка DaData: {error}")
        return 1
    except sqlite3.Error as error:
        print(f"Ошибка базы зон доставки: {error}")
        return 1

    print(f"Запрос: {query}")
    print(f"Нормализованный адрес: {checked.get('address') or 'не получен'}")
    print(f"Точность DaData (qc_geo): {checked.get('qc_geo', 'не получена')}")
    latitude = checked.get("geo_lat")
    longitude = checked.get("geo_lon")
    print(f"Широта: {latitude if latitude is not None else 'не получена'}")
    print(f"Долгота: {longitude if longitude is not None else 'не получена'}")
    if result.zone_id is not None:
        zone = next(
            (
                zone
                for zone in zones
                if int(zone["id"]) == result.zone_id
            ),
            None,
        )
        zone_name = zone["name"] if zone is not None else "неизвестная зона"
        print(f"Итог: зона определена — {zone_name} (ID {result.zone_id}).")
    else:
        status_messages = {
            "outside": "координаты не попали ни в одну активную зону",
            "ambiguous": "координаты попали на границу или в несколько зон",
            "low_accuracy": "DaData не вернула достаточно точные координаты",
        }
        print(
            "Итог: зона не определена — "
            f"{status_messages.get(result.zone_detection_status, 'причина неизвестна')}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_address_diagnostic())
