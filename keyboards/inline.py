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
    order_items: list[dict],
    supply_id: str,
    order_id: int,
    added_nm_ids: set[int] = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура со списком товаров в заказе для добавления в поставку.
    Каждая кнопка — товар (nmId). Добавленные товары помечаются галочкой.
    """
    if added_nm_ids is None:
        added_nm_ids = set()

    builder = InlineKeyboardBuilder()

    for item in order_items:
        nm_id = item.get("nmId", 0)
        barcode = item.get("barcode", "—")
        skus = item.get("skus", [])
        sku = skus[0] if skus else "—"
        added = "✅ " if nm_id in added_nm_ids else ""

        # Показываем nmId и первые символы баркода
        text = f"{added}Товар {nm_id} ({barcode[:10]}...)"
        builder.button(
            text=text,
            callback_data=f"add_item:{supply_id}:{order_id}:{nm_id}"
        )

    # Кнопка "Добавить все товары"
    builder.button(
        text="📥 Добавить все",
        callback_data=f"add_all:{supply_id}:{order_id}"
    )

    # Кнопка подтверждения
    builder.button(
        text="✅ Подтвердить",
        callback_data=f"confirm_supply:{supply_id}:{order_id}"
    )

    # Кнопка отмены
    builder.button(
        text="❌ Отменить",
        callback_data=f"cancel_supply:{supply_id}:{order_id}"
    )

    builder.adjust(2)
    return builder.as_markup()


def get_confirm_keyboard(supply_id: str, order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения создания поставки."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить поставку",
        callback_data=f"confirm_supply:{supply_id}:{order_id}"
    )
    builder.button(
        text="❌ Отмена",
        callback_data=f"cancel_supply:{supply_id}:{order_id}"
    )
    builder.adjust(2)
    return builder.as_markup()


def get_add_more_keyboard(supply_id: str, order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура: добавить ещё товары или подтвердить."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📥 Добавить ещё товары",
        callback_data=f"show_items:{supply_id}:{order_id}"
    )
    builder.button(
        text="✅ Подтвердить поставку",
        callback_data=f"confirm_supply:{supply_id}:{order_id}"
    )
    builder.adjust(2)
    return builder.as_markup()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню после успешной поставки."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Проверить новые заказы",
        callback_data="check_orders"
    )
    builder.adjust(1)
    return builder.as_markup()