import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from handlers import start_router, orders_router
from services.polling import get_polling_manager

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Создаём экземпляры бота и диспетчера
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# Регистрируем роутеры
dp.include_router(start_router)
dp.include_router(orders_router)

# Менеджер фоновых задач опроса
polling_manager = get_polling_manager()


async def on_startup():
    """Действия при запуске бота."""
    polling_manager.set_bot(bot)
    logger.info("Бот запущен! Ожидание пользователей...")


async def on_shutdown():
    """Действия при остановке бота."""
    logger.info("Останавливаю бота...")
    polling_manager.stop_all()
    logger.info("Все фоновые задачи остановлены.")


async def main():
    """Главная функция запуска бота."""
    # Регистрируем хуки жизненного цикла
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запускаем long-polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)