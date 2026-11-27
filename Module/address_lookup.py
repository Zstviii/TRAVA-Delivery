import os
from pathlib import Path

import httpx
from dadata import Dadata
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "Config" / ".env")


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
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("Не удалось проверить адрес через DaData.") from error
        if cleaned_address is None:
            raise RuntimeError("DaData не вернул результат проверки адреса.")

        return {
            "address": cleaned_address.get("result"),
            "geo_lat": cleaned_address.get("geo_lat"),
            "geo_lon": cleaned_address.get("geo_lon"),
            "qc_geo": cleaned_address.get("qc_geo"),
        }
