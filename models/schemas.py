from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class Order(BaseModel):
    """Модель заказа (сборочного задания) Wildberries."""
    id: int
    rid: Optional[str] = None
    createdAt: Optional[str] = None
    warehouseId: Optional[int] = None
    supplies: Optional[list[str]] = None
    nmId: Optional[int] = None
    skus: Optional[list[str]] = None
    totalPrice: Optional[int] = None
    discountPercent: Optional[float] = None
    officeId: Optional[int] = None
    barcode: Optional[str] = None


class Supply(BaseModel):
    """Модель поставки Wildberries."""
    id: str
    name: Optional[str] = None
    createdAt: Optional[str] = None
    destinationOfficeId: Optional[int] = None
    destinationWarehouseId: Optional[int] = None
    status: Optional[str] = None


class SupplyOrder(BaseModel):
    """Модель заказа внутри поставки."""
    orderId: int
    barcode: Optional[str] = None
    skus: Optional[list[str]] = None
    nmId: Optional[int] = None
    article: Optional[str] = None


class CreateSupplyResponse(BaseModel):
    """Ответ API при создании поставки."""
    id: str
    name: Optional[str] = None


class StickerRequest(BaseModel):
    """Запрос на получение стикеров."""
    orders: list[int] = Field(..., description="Список ID заказов")
    type: str = "png"  # png, pdf, zpl, svg


class StickerItem(BaseModel):
    """Один элемент ответа со стикером."""
    orderId: int
    file: Optional[str] = None  # base64
    partA: Optional[int] = None
    partB: Optional[int] = None
    barcode: Optional[str] = None


class StickerResponse(BaseModel):
    """Ответ API со стикерами."""
    stickers: list[StickerItem]


class UserData(BaseModel):
    """Данные пользователя бота."""
    api_key: str = ""
    supply_id: Optional[str] = None
    current_order_id: Optional[int] = None
    added_order_ids: list[int] = []
    notified_order_ids: list[int] = []  # ID заказов, о которых уже уведомили
    confirmed: bool = False
