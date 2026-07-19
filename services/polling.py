import asyncio
import logging
import os
import sys

# Гарантируем, что корень проекта находится в sys.path, чтобы импорты
# (wb_api, storage, keyboards и т.д.) разрешались как при запуске bot.py
# из корня проекта, так и при прямом запуске этого файла
# (python services/polling.py), когда в sys.path попадает только папка services/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from wb_api import WBApiClient, AuthError, RateLimitError
from storage.user_storage import get_storage
from keyboards.inline import get_create_supply_keyboard

logger = logging.getLogger(__name__)

# Unicode characters for messages
EM_DASH = "\u2014"  # —
BACK_ARROW = "\U0001F519"  # 🔄


def get_price(item: dict) -> int:
    """Получить цену из заказа (поддерживает разные форматы API)."""
    return item.get("finalPrice") or item.get("price") or item.get("totalPrice") or 0


def format_price(price: int) -> str:
    """Форматировать цену: если в копейках (целое число >= 100) — перевести в рубли."""
    if price is None:
        return "0"
    # Цена в копейках (обычно 51200 = 512.00 руб)
    if price >= 100:
        return f"{price / 100:.2f}".rstrip('0').rstrip('.')
    return str(price)


class PollingManager:
    """Менеджер фоновых задач опроса новых заказов."""

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}
        self._bot = None

    def set_bot(self, bot):
        """Установить ссылку на экземпляр бота."""
        self._bot = bot

    async def poll_new_orders(self, user_id: int, api_key: str, poll_interval: int = 30):
        """
        Фоновая задача: периодически проверяет новые заказы для пользователя.
        """
        client = WBApiClient(api_key)

        try:
            while True:
                try:
                    orders = await client.get_new_orders()
                    if orders and self._bot:
                        # Получаем названия товаров через content API (только subject)
                        nm_ids = [o.get("nmId") for o in orders if o.get("nmId")]
                        order_details = {}
                        if nm_ids:
                            try:
                                cards_data = await client.get_cards_list(nm_ids)
                                cards_list = []
                                if isinstance(cards_data, dict):
                                    cards_list = cards_data.get("cards", [])
                                    if not cards_list and "data" in cards_data:
                                        cards_list = cards_data["data"].get("cards", [])
                                elif isinstance(cards_data, list):
                                    cards_list = cards_data
                                for card in cards_list:
                                    nm_id = card.get("nmID")
                                    if nm_id:
                                        # Сохраняем только subject (название товара) из карточки
                                        # Цвет (colorCode) и артикул (article) берём из самого заказа
                                        order_details[nm_id] = {
                                            "nmId": nm_id,
                                            "subject": card.get("title") or card.get("name") or EM_DASH,
                                        }
                            except Exception as e:
                                logger.warning(f"Не удалось получить карточки товаров: {e}")

                        for order in orders:
                            order_id = order.get("id")
                            if order_id:
                                storage = get_storage()
                                user_data = storage.get_user(user_id)

                                # Пропускаем, если заказ уже обрабатывается или о нём уже уведомили
                                if order_id in user_data.added_order_ids or order_id in user_data.notified_order_ids:
                                    continue

                                nm_id = order.get("nmId", "?")
                                detail = order_details.get(nm_id, {})
                                
                                # Данные берём ПРЯМО из заказа (article, colorCode, skus)
                                subject = detail.get("subject", EM_DASH)
                                color = order.get("colorCode") or EM_DASH
                                article = order.get("article") or EM_DASH
                                total_price = get_price(order)

                                await self._bot.send_message(
                                    user_id,
                                    f"🆕 <b>Новый заказ!</b>\n\n"
                                    f"📦 ID заказа: <code>{order_id}</code>\n"
                                    f"🔖 Название: {subject}\n"
                                    f"🎨 Цвет: {color}\n"
                                    f"📄 Артикул: {article}\n"
                                    f"💰 Цена: {format_price(total_price)} ₽\n\n"
                                    f"Создайте поставку.\n\n"
                                    f"🔙 <i>Главное меню</i>",
                                    parse_mode="HTML",
                                    reply_markup=get_create_supply_keyboard(order_id)
                                )

                                # Сохраняем ID заказа в список уведомлённых, чтобы не дублировать
                                user_data.notified_order_ids.append(order_id)
                                storage.save_user(user_id, user_data)

                    await asyncio.sleep(poll_interval)

                except asyncio.CancelledError:
                    break
                except AuthError:
                    if self._bot:
                        await self._bot.send_message(
                            user_id,
                            "❌ <b>Ошибка авторизации.</b> Ваш API-ключ недействителен.\n"
                            "Пожалуйста, обновите ключ командой /set_key",
                            parse_mode="HTML"
                        )
                    break
                except RateLimitError:
                    logger.warning(f"Rate limit для пользователя {user_id}, ждём...")
                    await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"Ошибка при опросе заказов для {user_id}: {e}")
                    await asyncio.sleep(poll_interval)
        finally:
            await client.close()

    def start_polling(self, user_id: int, api_key: str, poll_interval: int = 30) -> asyncio.Task:
        """Запустить фоновый опрос заказов для пользователя."""
        # Отменяем старую задачу, если есть
        self.stop_polling(user_id)

        task = asyncio.create_task(
            self.poll_new_orders(user_id, api_key, poll_interval)
        )
        self._tasks[user_id] = task
        logger.info(f"Запущен опрос заказов для пользователя {user_id}")
        return task

    def stop_polling(self, user_id: int):
        """Остановить фоновый опрос заказов для пользователя."""
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            del self._tasks[user_id]
            logger.info(f"Остановлен опрос заказов для пользователя {user_id}")

    def stop_all(self):
        """Остановить все фоновые задачи."""
        for user_id, task in list(self._tasks.items()):
            task.cancel()
            logger.info(f"Остановлен опрос для пользователя {user_id}")
        self._tasks.clear()

    @property
    def active_users(self) -> list[int]:
        """Список пользователей с активным опросом."""
        return list(self._tasks.keys())


# Singleton
_polling_manager: PollingManager = None


def get_polling_manager() -> PollingManager:
    global _polling_manager
    if _polling_manager is None:
        _polling_manager = PollingManager()
    return _polling_manager