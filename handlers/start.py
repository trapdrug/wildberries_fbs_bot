import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from storage.user_storage import get_storage
from wb_api import WBApiClient, AuthError
from services.polling import get_polling_manager
from config import POLL_INTERVAL
from keyboards.reply import (
    get_main_reply_keyboard,
    remove_keyboard,
)

logger = logging.getLogger(__name__)

router = Router()


class Form(StatesGroup):
    """FSM состояния для регистрации API-ключа."""
    waiting_for_api_key = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    storage = get_storage()

    if storage.has_api_key(user_id):
        # Показываем главное меню и сообщение, что ключ уже активен
        await message.answer(
            "✅ <b>API-ключ уже активен!</b>\n\n"
            "Бот уже работает и отслеживает новые заказы.\n"
            "Используйте кнопки меню ниже для управления.",
            parse_mode="HTML",
            reply_markup=get_main_reply_keyboard()
        )
    else:
        await message.answer(
            "👋 <b>Добро пожаловать в бот для продавцов Wildberries (FBS)!</b>\n\n"
            "Для начала работы, пожалуйста, введите ваш API-ключ Wildberries.\n\n"
            " <b>Как получить ключ:</b>\n"
            "1. Перейдите в личный кабинет Wildberries\n"
            "2. Раздел «Настройки» → «API»\n"
            "3. Скопируйте токен категории «Marketplace»\n"
            "4. Отправьте его мне\n\n"
            "<i>Ключ будет сохранён и использован для всех запросов к API.</i>",
            parse_mode="HTML",
            reply_markup=remove_keyboard
        )
        await state.set_state(Form.waiting_for_api_key)


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    """Обработчик команды /help и кнопки Помощь."""
    help_text = (
        "📋 <b>Доступные команды:</b>\n\n"
        "/start — Начать работу\n"
        "/help — Показать эту справку\n"
        "/set_key — Сменить API-ключ\n"
        "/check — Проверить новые заказы вручную\n"
        "/status — Статус подключения к API\n\n"
        "<b>Как это работает:</b>\n"
        "1. Вы вводите API-ключ Wildberries\n"
        "2. Бот автоматически проверяет новые заказы каждые 30 сек\n"
        "3. При появлении заказа — бот предлагает создать поставку\n"
        "4. Вы добавляете товары в поставку и подтверждаете\n"
        "5. Бот генерирует QR-код поставки и штрих-коды заказов в PDF"
    )
    await message.answer(help_text, parse_mode="HTML", reply_markup=get_main_reply_keyboard())


@router.message(Command("set_key"))
@router.message(F.text == "🔑 Сменить API-ключ")
async def cmd_set_key(message: Message, state: FSMContext):
    """Сменить API-ключ."""
    await message.answer(
        "🔑 Введите новый API-ключ Wildberries:\n\n"
        "<i>Отправьте ключ одним сообщением.</i>",
        parse_mode="HTML",
        reply_markup=remove_keyboard
    )
    await state.set_state(Form.waiting_for_api_key)


@router.message(F.text == "🔙 Главное меню")
async def go_main_menu(message: Message):
    """Вернуться в главное меню."""
    await message.answer(
        "🔙 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard()
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Проверить статус подключения к API."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await message.answer(
            "❌ API-ключ не установлен. Используйте /set_key",
            parse_mode="HTML"
        )
        return

    client = WBApiClient(api_key)
    try:
        valid = await client.check_auth()
        if valid:
            await message.answer(
                "✅ <b>Подключение к Wildberries API активно.</b>\n"
                "Ключ валиден.",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "❌ <b>Не удалось подключиться к API.</b>\n"
                "Возможно, ключ недействителен или истёк.",
                parse_mode="HTML"
            )
    finally:
        await client.close()


@router.message(Form.waiting_for_api_key)
async def process_api_key(message: Message, state: FSMContext):
    """Обработка введённого API-ключа."""
    api_key = message.text.strip()

    if not api_key:
        await message.answer("❌ API-ключ не может быть пустым. Попробуйте ещё раз.")
        return

    # Проверяем ключ через тестовый запрос к API
    await message.answer("⏳ Проверяю API-ключ...")

    client = WBApiClient(api_key)
    try:
        valid = await client.check_auth()

        if valid:
            user_id = message.from_user.id
            storage = get_storage()
            storage.set_api_key(user_id, api_key)

            # Запускаем фоновый опрос заказов
            polling_manager = get_polling_manager()
            polling_manager.start_polling(user_id, api_key, POLL_INTERVAL)

            await message.answer(
                "✅ <b>API-ключ успешно сохранён!</b>\n\n"
                "Теперь я буду автоматически проверять новые заказы и уведомлять вас.\n\n"
                "Вы также можете проверить заказы вручную командой /check",
                parse_mode="HTML",
                reply_markup=get_main_reply_keyboard()
            )
            await state.clear()
        else:
            await message.answer(
                "❌ <b>Неверный API-ключ.</b>\n\n"
                "Проверьте правильность ключа и попробуйте снова.\n\n"
                "Убедитесь, что:\n"
                "• Вы скопировали токен категории «Marketplace»\n"
                "• Ключ активен и не истёк\n"
                "• У ключа есть права на чтение заказов",
                parse_mode="HTML"
            )
    except AuthError:
        await message.answer(
            "❌ <b>Ошибка аутентификации (401).</b>\n"
            "Проверьте правильность API-ключа.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка проверки ключа: {e}")
        await message.answer(
            f"❌ <b>Ошибка подключения:</b> {e}\n\n"
            "Попробуйте ещё раз позже.",
            parse_mode="HTML"
        )
    finally:
        await client.close()