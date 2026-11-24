import os
from datetime import date
from pathlib import Path

import requests
from dadata import Dadata
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "Config" / ".env")

BASE_URL = os.getenv("POSIFLORA_BASE_URL")


def _get_base_url() -> str:
    if not BASE_URL:
        raise RuntimeError("Не задан POSIFLORA_BASE_URL.")
    return BASE_URL.rstrip("/")


class PosiFloraAPI:
    def __init__(self):
        self.session = requests.Session()

    @staticmethod
    def _auth_headers():
        api_key = os.getenv("POSIFLORA_API_KEY")
        if not api_key:
            raise RuntimeError("Не задан POSIFLORA_API_KEY.")

        return {
            "X-Api-Key": api_key,
            "Accept": "application/vnd.api+json",
        }

    def get_orders(
        self,
        delivery_date: date | None = None,
        pageSize: int = 300,
        BoolDelivery: str = "true",
    ):
        """Получение списка заказов с возможностью выбора даты доставки и количества заказов на странице.

        Принимает:
            delivery_date: date - Дата доставки, по умолчанию текущая дата;
            pageSize: int - Количество заказов на странице, по умолчанию 300;
            BoolDelivery: str - Фильтр заказов с доставкой, по умолчанию "true".

        Выполняет:
            Передаёт API-ключ в заголовке X-Api-Key;
            Формирует параметры запроса с фильтрацией по дате доставки, наличию доставки и количеству заказов;
            Отправляет GET-запрос к API Posiflora для получения списка заказов;
            Проверяет успешность выполнения запроса.

        Возвращает:
            list - Список заказов из ответа API (data).
            Если список заказов отсутствует, возвращает пустой список.

        Вызывает исключение:
            RuntimeError - Если API возвращает ошибку HTTP.
        """

        if delivery_date is None:
            delivery_date = date.today()

        params = {
            "filter[dueTime][from]": delivery_date.isoformat(),
            "filter[dueTime][to]": delivery_date.isoformat(),
            "filter[delivery]": BoolDelivery,
            "page[size]": pageSize,
        }

        response = self.session.get(
            f"{_get_base_url()}/v1/orders",
            params=params,
            headers=self._auth_headers(),
            timeout=20,
        )

        if not response.ok:
            raise RuntimeError(
                f"Ошибка Posiflora: HTTP {response.status_code}. "
                f"{response.text[:1000]}"
            )

        result = response.json()

        return result.get("data", [])

    def update_deliveryStatus(self, OrderID=None, status=None):
        """Изменение статуса доставки заказа.

        Принимает:
            OrderID: str - ID заказа, статус доставки которого необходимо изменить;
            status: str - Новый статус доставки заказа:
                "courier" - Заказ передан курьеру;
                "delivered" - Заказ доставлен;
                "cannotBeDelivered" - Заказ не удалось доставить.

        Выполняет:
            Проверяет наличие ID заказа и нового статуса;
            Проверяет, что указанный статус входит в список допустимых;
            Передаёт API-ключ в заголовке X-Api-Key;
            Формирует POST-запрос с новым статусом доставки;
            Отправляет запрос к API Posiflora;
            Проверяет успешность выполнения запроса.

        Возвращает:
            dict - Ответ API Posiflora в формате JSON.

        Вызывает исключение:
            RuntimeError - Если отсутствует ID заказа;
            RuntimeError - Если не указан статус доставки;
            RuntimeError - Если указан недопустимый статус;
            RuntimeError - Если API возвращает ошибку.
        """

        if OrderID is None:
            raise RuntimeError("Нет ID заказа")

        if status is None:
            raise RuntimeError("Нет статуса заказа")

        if status not in ("courier", "delivered", "cannotBeDelivered"):
            raise RuntimeError("Указан неверный статус доставки")

        response = self.session.post(
            f"{_get_base_url()}/v1/orders/{OrderID}/delivery-status",
            json={
                "data": {
                    "type": "orders-delivery-status",
                    "id": OrderID,
                    "attributes": {"deliveryStatus": status},
                }
            },
            headers={
                **self._auth_headers(),
                "Content-Type": "application/vnd.api+json",
            },
            timeout=20,
        )

        print("HTTP:", response.status_code)
        print("URL:", response.url)

        if not response.ok:
            try:
                result = response.json()
                errors = result.get("errors", [])
                message = "; ".join(
                    error.get("title", "Неизвестная ошибка") for error in errors
                )
            except ValueError:
                message = "Сервер вернул ответ в неожиданном формате"

            raise RuntimeError(
                f"Ошибка изменения статуса доставки Posiflora: "
                f"HTTP {response.status_code}, {message}"
            )

        if not response.content:
            return {}

        return response.json()


class DadataAPI:
    @staticmethod
    def check_adress(address: str) -> dict:
        """Проверка адреса через API DaData.

        Принимает:
            address - Строка с адресом для проверки.

        Выполняет:
            Отправляет POST-запрос к API DaData для проверки адреса;
            Проверяет успешность выполнения запроса;
            Обрабатывает ошибки и формирует сообщение об ошибке;
            Возвращает результат проверки адреса в виде словаря координат.
        """
        token = os.getenv("DADATA_API")
        secret = os.getenv("DADATA_SECRET")
        if not address:
            raise ValueError("Адрес не может быть пустым.")
        if not token:
            raise RuntimeError("Не задан токен DaData.")

        dadata = Dadata(token, secret)
        cleaned_address = dadata.clean("address", address)
        if cleaned_address is None:
            raise RuntimeError("DaData не вернул результат проверки адреса.")

        geoLat = cleaned_address.get("geo_lat")
        geoLon = cleaned_address.get("geo_lon")
        qc_geo = cleaned_address.get("qc_geo")
        return {"geo_lat": geoLat, "geo_lon": geoLon, "qc_geo": qc_geo}


if __name__ == "__main__":
    print(PosiFloraAPI().get_orders(date(2026, 11, 2)))
