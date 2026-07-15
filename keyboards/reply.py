from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главная reply-клавиатура (выпадающее меню внизу)."""
    buttons = [
        [KeyboardButton(text="🔍 Проверить заказы")],
        [KeyboardButton(text="🔑 Сменить API-ключ")],
        [KeyboardButton(text="📊 Статус подключения")],
        [KeyboardButton(text="❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_check_orders_reply_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура после проверки заказов."""
    buttons = [
        [KeyboardButton(text="📦 Создать поставку")],
        [KeyboardButton(text="❌ Пропустить")],
        [KeyboardButton(text="🔙 Главное меню")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_supply_items_reply_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для работы с товарами в поставке."""
    buttons = [
        [KeyboardButton(text="✅ Добавить все")],
        [KeyboardButton(text="✅ Подтвердить поставку")],
        [KeyboardButton(text="❌ Отменить")],
        [KeyboardButton(text="🔙 Главное меню")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


def get_confirm_reply_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура подтверждения поставки."""
    buttons = [
        [KeyboardButton(text="✅ Подтвердить поставку")],
        [KeyboardButton(text="❌ Отменить")],
        [KeyboardButton(text="🔙 Главное меню")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


remove_keyboard = ReplyKeyboardRemove()