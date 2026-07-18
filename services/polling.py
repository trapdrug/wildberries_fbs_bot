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
                        # Получаем детальную информацию о товарах (название, цвет, артикул)
                        nm_ids = [o.get("nmId") for o in orders if o.get("nmId")]
                        order_details = {}
                        if nm_ids:
                            try:
                                details = await client.get_orders_status(nm_ids)
                                for d in details:
                                    nm = d.get("nmId")
                                    if nm:
                                        order_details[nm] = d
                            except Exception as e:
                                logger.warning(f"Не удалось получить детали заказов: {e}")

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
                                subject = detail.get("subject") or "—"
                                color = detail.get("color") or "—"
                                supplier_article = detail.get("supplierArticle") or "—"
                                total_price = order.get("totalPrice", "—")

                                await self._bot.send_message(
                                    user_id,
                                    f"🆕 <b>Новый заказ!</b>\n\n"
                                    f"📦 ID заказа: <code>{order_id}</code>\n"
                                    f"🔖 Название: {subject}\n"
                                    f"🎨 Цвет: {color}\n"
                                    f"📄 Артикул: {supplier_article}\n"
                                    f"💰 Цена: {total_price} ₽\n\n"
                                    "Создайте поставку.\n\n"
                                    "🔙 <i>Главное меню</i>",
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