import logging
import base64
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from aiohttp import ClientTimeout, ClientResponseError

from config import API_HOST, API_VERSION

logger = logging.getLogger(__name__)


class WildberriesAPIError(Exception):
    """Базовое исключение для ошибок API Wildberries."""
    def __init__(self, message: str, status_code: int = None, body: str = None):
        self.status_code = status_code
        self.body = body
        super().__init__(message)


class AuthError(WildberriesAPIError):
    """Ошибка аутентификации (401)."""
    pass


class ConflictError(WildberriesAPIError):
    """Конфликт статусов (409)."""
    pass


class RateLimitError(WildberriesAPIError):
    """Превышение лимита запросов (429)."""
    pass


class NotFoundError(WildberriesAPIError):
    """Ресурс не найден (404)."""
    pass


class ForbiddenError(WildberriesAPIError):
    """Доступ запрещён (403)."""
    pass


class WBApiClient:
    """Асинхронный клиент для работы с API Wildberries (поставщики)."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = urljoin(API_HOST, f"/api/{API_VERSION}/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # API Wildberries требует заголовок Authorization с Bearer-префиксом
            auth_header = f"Bearer {self.api_key}" if not self.api_key.startswith("Bearer ") else self.api_key
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=30),
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> dict:
        """Выполнить HTTP-запрос к API с обработкой ошибок."""
        session = await self._get_session()
        url = urljoin(self.base_url, path.lstrip("/"))

        logger.debug(f"{method} {url}")

        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 200 or resp.status == 201:
                    return await resp.json() if resp.content_type == "application/json" else {}

                body_text = await resp.text()

                if resp.status == 401:
                    raise AuthError("Неверный API-ключ", status_code=401, body=body_text)
                elif resp.status == 403:
                    raise ForbiddenError("Доступ запрещён", status_code=403, body=body_text)
                elif resp.status == 404:
                    raise NotFoundError("Ресурс не найден", status_code=404, body=body_text)
                elif resp.status == 409:
                    raise ConflictError("Конфликт статусов", status_code=409, body=body_text)
                elif resp.status == 429:
                    raise RateLimitError("Превышен лимит запросов", status_code=429, body=body_text)
                else:
                    raise WildberriesAPIError(
                        f"Ошибка API: {resp.status}",
                        status_code=resp.status,
                        body=body_text
                    )
        except (ClientResponseError, aiohttp.ClientError) as e:
            raise WildberriesAPIError(f"Сетевая ошибка: {e}")

    async def check_auth(self) -> bool:
        """Проверить валидность API-ключа через получение новых заказов."""
        try:
            await self.get_new_orders()
            return True
        except AuthError:
            return False
        except Exception as e:
            logger.warning(f"Ошибка при проверке ключа: {e}")
            return False

    async def get_new_orders(self) -> list[dict]:
        """
        Получить новые сборочные задания (заказы).
        GET /api/v3/orders/new
        """
        data = await self._request("GET", "orders/new")
        return data.get("orders", [])

    async def get_orders(self, **params) -> list[dict]:
        """
        Получить список сборочных заданий с фильтрами.
        GET /api/v3/orders
        """
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"orders?{query}" if query else "orders"
        data = await self._request("GET", path)
        return data.get("orders", [])

    async def get_orders_client(self, order_ids: list[int]) -> list[dict]:
        """
        Получить информацию о клиентах по заказам.
        POST /api/v3/orders/client
        """
        if not order_ids:
            return []
        payload = {"orderIds": order_ids}
        data = await self._request("POST", "orders/client", json=payload)
        return data
    
    async def get_supplies(self) -> list[dict]:
        """
        Получить список поставок.
        GET /api/v3/supplies
        """
        data = await self._request("GET", "supplies")
        return data.get("supplies", [])

    async def get_supply_info(self, supply_id: str) -> dict:
        """
        Получить информацию о поставке.
        GET /api/v3/supplies/{supplyId}
        """
        path = f"supplies/{supply_id}"
        return await self._request("GET", path)

    async def get_supply_order_ids(self, supply_id: str) -> list[int]:
        """
        Получить ID сборочных заданий в поставке.
        GET /api/marketplace/v3/supplies/{supplyId}/order-ids
        """
        path = f"supplies/{supply_id}/order-ids"
        # Используем marketplace base
        data = await self._request("GET", path)
        return data.get("orderIds", [])

    async def get_supply_barcode(self, supply_id: str) -> Optional[str]:
        """
        Получить QR-код поставки (base64).
        GET /api/v3/supplies/{supplyId}/barcode
        """
        path = f"supplies/{supply_id}/barcode"
        data = await self._request("GET", path)
        file_b64 = data.get("file")
        if file_b64:
            return base64.b64decode(file_b64)
        return None

    async def delete_supply(self, supply_id: str) -> dict:
        """
        Удалить поставку.
        DELETE /api/v3/supplies/{supplyId}
        """
        path = f"supplies/{supply_id}"
        return await self._request("DELETE", path)

    async def add_orders_to_supply(self, supply_id: str, order_ids: list[int]) -> dict:
        """
        Добавить сборочные задания к поставке (замена всех).
        PATCH /api/marketplace/v3/supplies/{supplyId}/orders
        """
        path = f"supplies/{supply_id}/orders"
        payload = {"orderIds": order_ids}
        return await self._request("PATCH", path, json=payload)

    async def create_trbx(self, supply_id: str) -> dict:
        """
        Создать грузоместо в поставке.
        POST /api/v3/supplies/{supplyId}/trbx
        """
        path = f"supplies/{supply_id}/trbx"
        return await self._request("POST", path, json={})

    async def delete_trbx(self, supply_id: str, trbx_id: str) -> dict:
        """
        Удалить грузоместо из поставки.
        DELETE /api/v3/supplies/{supplyId}/trbx
        """
        path = f"supplies/{supply_id}/trbx"
        payload = {"trbxIds": [trbx_id]}
        return await self._request("DELETE", path, json=payload)

    async def get_trbx_stickers(self, supply_id: str, trbx_ids: list[str]) -> list[bytes]:
        """
        Получить стикеры грузомест.
        POST /api/v3/supplies/{supplyId}/trbx/stickers
        """
        path = f"supplies/{supply_id}/trbx/stickers"
        payload = {"trbxIds": trbx_ids, "type": "png"}
        data = await self._request("POST", path, json=payload)
        stickers = data.get("stickers", [])
        result = []
        for s in stickers:
            file_b64 = s.get("file")
            if file_b64:
                try:
                    result.append(base64.b64decode(file_b64))
                except:
                    pass
        return result

    async def get_offices(self) -> list[dict]:
        """
        Получить список складов (офисов) продавца.
        GET /api/v3/offices
        """
        data = await self._request("GET", "offices")
        return data.get("offices", [])

    async def get_warehouses(self) -> list[dict]:
        """
        Получить список складов Wildberries.
        GET /api/v3/warehouses
        """
        data = await self._request("GET", "warehouses")
        return data.get("warehouses", [])

    async def get_orders_status(self, nm_ids: list[int]) -> list[dict]:
        """
        Получить детальную информацию о товарах (название, цвет, артикул).
        POST /api/v3/orders/status
        """
        if not nm_ids:
            return []
        payload = {"nmIds": nm_ids}
        data = await self._request("POST", "orders/status", json=payload)
        return data.get("orders", [])

    async def create_supply(self) -> dict:
        """
        Создать новую поставку.
        POST /api/v3/supplies
        ID офиса приёмки определяется автоматически WB.
        """
        return await self._request("POST", "supplies", json={})

    async def add_order_to_supply(self, supply_id: str, order_id: int) -> dict:
        """
        Добавить заказ в поставку.
        POST /api/v3/supplies/{supplyId}/orders
        """
        path = f"supplies/{supply_id}/orders"
        payload = {"orders": [str(order_id)]}
        return await self._request("POST", path, json=payload)

    async def get_supply_orders(self, supply_id: str) -> list[dict]:
        """
        Получить список заказов в поставке.
        GET /api/v3/supplies/{supplyId}/orders
        """
        path = f"supplies/{supply_id}/orders"
        data = await self._request("GET", path)
        return data.get("orders", [])

    async def get_orders_stickers(
        self,
        order_ids: list[int],
        sticker_type: str = "png"
    ) -> list[dict]:
        """
        Получить стикеры для заказов.
        POST /api/v3/orders/stickers
        """
        payload = {
            "orders": order_ids,
            "type": sticker_type
        }
        data = await self._request("POST", "orders/stickers", json=payload)
        return data.get("stickers", [])

    async def confirm_supply(self, supply_id: str) -> dict:
        """
        Подтвердить/передать поставку в доставку.
        PATCH /api/v3/supplies/{supplyId}/deliver
        """
        path = f"supplies/{supply_id}/deliver"
        return await self._request("PATCH", path)

    @staticmethod
    def decode_sticker_file(sticker_data: dict) -> Optional[bytes]:
        """Декодировать base64-изображение стикера."""
        file_b64 = sticker_data.get("file")
        if file_b64:
            try:
                return base64.b64decode(file_b64)
            except Exception as e:
                logger.error(f"Ошибка декодирования стикера: {e}")
        return None
