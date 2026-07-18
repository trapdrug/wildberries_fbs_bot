from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_create_supply_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура: создать поставку для нового заказа."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📦 Создать поставку",
        callback_data=f"create_supply:{order_id}"
    )
    builder.button(
        text="❌ Пропустить",
        callback_data=f"skip_order:{order_id}"
    )
    builder.adjust(2)
    return builder.as_markup()


def get_order_items_keyboard(
    items: list[dict],
    order_details: dict,
    selected_orders: set[int],
) -> InlineKeyboardMarkup:
    """
    Клавиатура с чекбоксами товаров.
    Каждый товар — кнопка с переключением выбора.
    Кнопка «Далее» только если выбраны товары.
    """
    builder = InlineKeyboardBuilder()

    for item in items:
        item_id = item.get("id")
        nm_id = item.get("nmId")
        detail = order_details.get(nm_id, {})
        subject = detail.get("subject") or f"Товар {nm_id}"
        checked = "✅" if item_id in selected_orders else "⬜"
        builder.button(
            text=f"{checked} {subject}",
            callback_data=f"toggle_item:{item_id}"
        )

    # Кнопка "Далее" доступна только если есть выбранные товары
    if selected_orders:
        builder.button(
            text="➡️ Далее",
            callback_data="next_to_trbx"
        )
    else:
        builder.button(
            text="⬜ Выберите товары",
            callback_data="noop"
        )

    builder.adjust(2)
    return builder.as_markup()


def get_trbx_keyboard(supply_id: str, trbx_list: list[dict]) -> InlineKeyboardMarkup:
    """
    Клавиатура управления грузоместами.
    """
    builder = InlineKeyboardBuilder()

    for trbx in trbx_list:
        num = trbx.get("number", "?")
        trbx_id = trbx.get("id", "")
        builder.button(
            text=f"❌ Грузоместо #{num}",
            callback_data=f"del_trbx:{trbx_id}"
        )

    builder.button(
        text="➕ Добавить грузоместо",
        callback_data=f"add_trbx:{supply_id}"
    )

    if trbx_list:
        builder.button(
            text="✅ Завершить",
            callback_data="finish_supply"
        )

    builder.adjust(2)
    return builder.as_markup()


def get_confirm_supply_keyboard(supply_id: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения поставки."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Завершить поставку",
        callback_data="finish_supply"
    )
    builder.button(
        text="📦 Добавить грузоместо",
        callback_data=f"add_trbx:{supply_id}"
    )
    builder.adjust(2)
    return builder.as_markup()


def get_supply_items_keyboard(supply_id: str, order_ids: list[int]) -> InlineKeyboardMarkup:
    """Клавиатура для списка товаров в поставке."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📦 Добавить грузоместо",
        callback_data=f"add_trbx:{supply_id}"
    )
    builder.button(
        text="✅ Завершить",
        callback_data="finish_supply"
    )
    builder.adjust(2)
    return builder.as_markup()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Проверить новые заказы",
        callback_data="check_orders"
    )
    builder.adjust(1)
    return builder.as_markup()