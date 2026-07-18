import asyncio
import logging
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, FSInputFile, CallbackQuery

from config import POLL_INTERVAL, DESTINATION_OFFICE_ID, STICKERS_DIR
from storage.user_storage import get_storage
from wb_api import (
    WBApiClient,
    AuthError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    WildberriesAPIError,
)
from keyboards.reply import (
    get_main_reply_keyboard,
    get_check_orders_reply_keyboard,
    get_supply_items_reply_keyboard,
    get_confirm_reply_keyboard,
    remove_keyboard,
)
from models.schemas import UserData
from utils.stickers import StickerGenerator
from services.polling import get_polling_manager

logger = logging.getLogger(__name__)

router = Router()
sticker_gen = StickerGenerator(output_dir=STICKERS_DIR)


class OrderFSM(StatesGroup):
    """FSM состояния для работы с заказами и поставками."""
    idle = State()
    waiting_for_destination_office = State()
    creating_supply = State()
    selecting_items = State()
    confirming = State()
    waiting_for_office_id = State()


# Словарь для хранения данных о заказах в процессе обработки
# {user_id: {"supply_id": str, "order_ids": list[int], "added_items": set[int], "items": list[dict]}}
processing_data: dict[int, dict] = {}


# --- Вспомогательная функция для проверки заказов ---

async def _check_orders_logic(message: Message):
    """Общая логика проверки заказов (используется из разных мест)."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await message.answer(
            "❌ Сначала установите API-ключ через /start или /set_key",
            parse_mode="HTML"
        )
        return

    await message.answer("🔍 Проверяю новые заказы...")

    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        if orders:
            # Получаем детальную информацию о товарах
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

            text = f"<b>Найдено заказов: {len(orders)}</b>\n\n"
            for order in orders:
                order_id = order.get("id")
                nm_id = order.get("nmId", "?")
                detail = order_details.get(nm_id, {})
                subject = detail.get("subject") or "—"
                brand = detail.get("brand") or "—"
                color = detail.get("color") or "—"
                supplier_article = detail.get("supplierArticle") or "—"
                text += (
                    f"• Заказ <code>{order_id}</code>\n"
                    f"  📦 {subject} ({brand})\n"
                    f"  🎨 {color} | 📄 {supplier_article}\n\n"
                )

            await message.answer(text, parse_mode="HTML")

            # Сохраняем первый заказ для создания поставки
            first_order = orders[0]
            first_order_id = first_order.get("id")
            processing_data[user_id] = {
                "order_id": first_order_id,
                "pending_create": True,
            }

            # Reply-клавиатура: Создать поставку / Пропустить
            await message.answer(
                "Создать поставку для первого заказа?",
                reply_markup=get_check_orders_reply_keyboard()
            )
        else:
            await message.answer(
                "✅ Новых заказов нет.",
                parse_mode="HTML",
                reply_markup=get_main_reply_keyboard()
            )
    except AuthError:
        await message.answer(
            "❌ Ошибка авторизации API. Проверьте ключ командой /status",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка проверки заказов: {e}")
        await message.answer(f"❌ Ошибка: {e}", parse_mode="HTML")
    finally:
        await client.close()


# --- Обработчики команд, работающие в любом состоянии FSM ---

@router.message(StateFilter("*"), Command("check"))
async def cmd_check_orders_any_state(message: Message, state: FSMContext):
    """Ручная проверка новых заказов в любом состоянии."""
    await _check_orders_logic(message)


# --- Callback-обработчики для inline-кнопок ---

@router.callback_query(F.data.startswith("create_supply:"))
async def cb_create_supply(callback: CallbackQuery):
    """Обработчик inline-кнопки 'Создать поставку' из уведомления о новом заказе."""
    await callback.answer()  # Отвечаем на callback

    user_id = callback.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await callback.message.answer("❌ API-ключ не найден. Используйте /set_key")
        return

    # Получаем order_id из callback_data
    try:
        order_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.message.answer("❌ Ошибка: неверный ID заказа.")
        return

    # Если не указан officeId по умолчанию — запрашиваем
    if DESTINATION_OFFICE_ID == 0:
        processing_data[user_id] = {
            "order_id": order_id,
            "pending_create": True,
        }
        await callback.message.answer(
            "🏢 <b>Укажите ID офиса приёмки Wildberries</b>\n\n"
            "Это номер офиса, куда вы будете привозить товар.\n"
            "Его можно найти в личном кабинете WB в разделе поставок.\n\n"
            "Введите число:",
            parse_mode="HTML",
            reply_markup=remove_keyboard
        )
        return

    # Создаём поставку сразу
    await proceed_create_supply(callback.message, user_id, api_key, order_id, DESTINATION_OFFICE_ID)


@router.callback_query(F.data.startswith("skip_order:"))
async def cb_skip_order(callback: CallbackQuery):
    """Обработчик inline-кнопки 'Пропустить' из уведомления о новом заказе."""
    await callback.answer()

    user_id = callback.from_user.id
    if user_id in processing_data:
        del processing_data[user_id]

    await callback.message.answer(
        "⏭ Заказ пропрошен.\n"
        "Я продолжу отслеживать новые заказы.",
        reply_markup=get_main_reply_keyboard()
    )


@router.callback_query(F.data.startswith("add_item:"))
async def cb_add_item(callback: CallbackQuery):
    """Добавить отдельный товар в поставку (inline-кнопка)."""
    await callback.answer()

    user_id = callback.from_user.id
    if user_id not in processing_data:
        await callback.message.answer("❌ Нет активной поставки.")
        return

    try:
        parts = callback.data.split(":")
        supply_id = parts[1]
        order_id = int(parts[2])
        nm_id = int(parts[3])
    except (IndexError, ValueError):
        await callback.message.answer("❌ Ошибка: неверные параметры.")
        return

    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await callback.message.answer("❌ API-ключ не найден.")
        return

    client = WBApiClient(api_key)
    try:
        await client.add_order_to_supply(supply_id, order_id)
        processing_data[user_id]["added_items"].add(nm_id)

        # Обновляем storage
        user_data = storage.get_user(user_id)
        if order_id not in user_data.added_order_ids:
            user_data.added_order_ids.append(order_id)
        storage.save_user(user_id, user_data)

        await callback.message.answer(
            f"✅ Товар {nm_id} добавлен в поставку.",
            reply_markup=get_supply_items_reply_keyboard()
        )
    except ConflictError:
        await callback.message.answer("⚠️ Этот заказ уже добавлен в поставку.")
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")
        await callback.message.answer(f"❌ Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("add_all:"))
async def cb_add_all(callback: CallbackQuery):
    """Добавить все товары в поставку (inline-кнопка)."""
    await callback.answer()

    user_id = callback.from_user.id
    if user_id not in processing_data:
        await callback.message.answer("❌ Нет активной поставки.")
        return

    try:
        parts = callback.data.split(":")
        supply_id = parts[1]
        order_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.message.answer("❌ Ошибка: неверные параметры.")
        return

    # Перенаправляем на reply-обработчик
    await msg_add_all(callback.message)


@router.callback_query(F.data.startswith("confirm_supply:"))
async def cb_confirm_supply(callback: CallbackQuery):
    """Подтвердить поставку (inline-кнопка)."""
    await callback.answer()

    user_id = callback.from_user.id
    if user_id not in processing_data:
        await callback.message.answer("❌ Нет активной поставки.")
        return

    # Перенаправляем на reply-обработчик
    await msg_confirm_supply(callback.message)


@router.callback_query(F.data.startswith("cancel_supply:"))
async def cb_cancel_supply(callback: CallbackQuery):
    """Отменить поставку (inline-кнопка)."""
    await callback.answer()

    user_id = callback.from_user.id
    if user_id in processing_data:
        del processing_data[user_id]

    await callback.message.answer(
        "❌ Поставка отменена.",
        reply_markup=get_main_reply_keyboard()
    )


@router.callback_query(F.data == "check_orders")
async def cb_check_orders(callback: CallbackQuery):
    """Проверить заказы (inline-кнопка)."""
    await callback.answer()
    await _check_orders_logic(callback.message)


@router.message(F.text == "🔍 Проверить заказы")
async def msg_check_orders_button(message: Message):
    """Проверить заказы (из reply-кнопки)."""
    await _check_orders_logic(message)


# --- Обработчик: Создать поставку (reply-кнопка) ---

@router.message(F.text == "📦 Создать поставку")
async def msg_create_supply(message: Message, state: FSMContext):
    """Создать поставку (из reply-кнопки)."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await message.answer("❌ API-ключ не найден. Используйте /set_key")
        return

    # Получаем order_id из временного хранилища
    order_id = processing_data.get(user_id, {}).get("order_id")
    if not order_id:
        await message.answer("❌ Нет заказа для создания поставки. Проверьте заказы сначала.")
        return

    # Если не указан officeId по умолчанию — запрашиваем
    if DESTINATION_OFFICE_ID == 0:
        await message.answer(
            "🏢 <b>Укажите ID офиса приёмки Wildberries</b>\n\n"
            "Это номер офиса, куда вы будете привозить товар.\n"
            "Его можно найти в личном кабинете WB в разделе поставок.\n\n"
            "Введите число:",
            parse_mode="HTML",
            reply_markup=remove_keyboard
        )
        await state.set_state(OrderFSM.waiting_for_office_id)
        return

    # Создаём поставку сразу
    await proceed_create_supply(message, user_id, api_key, order_id, DESTINATION_OFFICE_ID)


@router.message(OrderFSM.waiting_for_office_id, F.text.startswith("/"))
async def process_office_id_command(message: Message, state: FSMContext):
    """Обработка команды в состоянии ввода ID офиса."""
    await message.answer(
        "⚠️ <b>Вы ввели команду, но сейчас ожидается ID офиса приёмки.</b>\n\n"
        "Если хотите отменить ввод, нажмите «Главное меню».",
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard()
    )
    await state.clear()


@router.message(OrderFSM.waiting_for_office_id)
async def process_office_id(message: Message, state: FSMContext):
    """Обработка ввода ID офиса приёмки."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await message.answer("❌ API-ключ не найден. Используйте /set_key")
        await state.clear()
        return

    try:
        office_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число (ID офиса приёмки).")
        return

    order_id = processing_data.get(user_id, {}).get("order_id")
    if not order_id:
        await message.answer("❌ Ошибка: заказ не найден. Начните заново.")
        await state.clear()
        return

    await proceed_create_supply(message, user_id, api_key, order_id, office_id)
    await state.clear()


async def proceed_create_supply(msg, user_id: int, api_key: str, order_id: int, office_id: int):
    """Создать поставку через API."""
    await msg.answer("⏳ Создаю поставку...")

    client = WBApiClient(api_key)
    try:
        # 1. Создаём поставку
        supply_data = await client.create_supply(office_id)
        supply_id = supply_data.get("id")

        if not supply_id:
            await msg.answer("❌ Ошибка: не получен ID поставки от Wildberries.")
            return

        # 2. Получаем информацию о заказе
        new_orders = await client.get_new_orders()
        current_order = None
        for o in new_orders:
            if o.get("id") == order_id:
                current_order = o
                break

        if not current_order:
            await msg.answer(
                f"❌ Заказ {order_id} не найден в списке новых. Возможно, он уже обработан.",
                parse_mode="HTML"
            )
            return

        # 3. Сохраняем данные
        processing_data[user_id] = {
            "supply_id": supply_id,
            "order_ids": [order_id],
            "added_items": set(),
            "items": [current_order],
            "pending_create": False,
        }

        # 4. Обновляем storage
        storage = get_storage()
        user_data = storage.get_user(user_id)
        user_data.supply_id = supply_id
        user_data.current_order_id = order_id
        storage.save_user(user_id, user_data)

        # Получаем детальную информацию о товаре
        nm_id = current_order.get("nmId")
        detail = {}
        if nm_id:
            try:
                details = await client.get_orders_status([nm_id])
                if details:
                    detail = details[0]
            except Exception as e:
                logger.warning(f"Не удалось получить детали товара {nm_id}: {e}")

        subject = detail.get("subject") or "—"
        brand = detail.get("brand") or "—"
        color = detail.get("color") or "—"
        supplier_article = detail.get("supplierArticle") or "—"
        tech_size = detail.get("techSize") or "—"
        total_price = current_order.get("totalPrice", "—")

        # 5. Показываем информацию о поставке с reply-клавиатурой
        await msg.answer(
            f"✅ <b>Поставка создана!</b>\n\n"
            f"📦 ID поставки: <code>{supply_id}</code>\n"
            f"🆔 Заказ: <code>{order_id}</code>\n\n"
            f"<b>Товары в заказе:</b>\n"
            f"🔖 Название: {subject}\n"
            f"🏷 Бренд: {brand}\n"
            f"🎨 Цвет: {color}\n"
            f"📐 Размер: {tech_size}\n"
            f"📄 Артикул: {supplier_article}\n"
            f"💰 Цена: {total_price} ₽\n\n"
            "Используйте кнопки ниже для управления:",
            parse_mode="HTML",
            reply_markup=get_supply_items_reply_keyboard()
        )

    except ConflictError as e:
        await msg.answer(
            f"❌ <b>Ошибка 409 (Конфликт).</b>\n"
            f"Возможно, заказ {order_id} уже добавлен в другую поставку.\n\n"
            f"Детали: {e.body[:200] if e.body else 'Нет данных'}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка создания поставки: {e}")
        await msg.answer(
            f"❌ <b>Ошибка создания поставки:</b> {e}",
            parse_mode="HTML"
        )
    finally:
        await client.close()


# --- Обработчик: Добавить все товары (reply-кнопка) ---

@router.message(F.text == "✅ Добавить все")
async def msg_add_all(message: Message):
    """Добавить все товары заказа в поставку."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if user_id not in processing_data or processing_data[user_id].get("pending_create", True):
        await message.answer("❌ Нет активной поставки. Сначала проверьте заказы.")
        return

    if not api_key:
        await message.answer("❌ API-ключ не найден.")
        return

    data = processing_data[user_id]
    supply_id = data.get("supply_id")
    order_ids = data.get("order_ids", [])

    if not order_ids or not supply_id:
        await message.answer("❌ Нет заказов для добавления.")
        return

    client = WBApiClient(api_key)
    try:
        # Добавляем все заказы в поставку
        for oid in order_ids:
            try:
                await client.add_order_to_supply(supply_id, oid)
            except ConflictError:
                pass  # Уже добавлен

        # Отмечаем все товары как добавленные
        for item in data["items"]:
            nm_id = item.get("nmId")
            if nm_id:
                data["added_items"].add(nm_id)

        # Обновляем storage
        user_data = storage.get_user(user_id)
        for oid in order_ids:
            if oid not in user_data.added_order_ids:
                user_data.added_order_ids.append(oid)
        storage.save_user(user_id, user_data)

        added_count = len(data["added_items"])
        await message.answer(
            f"✅ <b>Все товары добавлены в поставку!</b>\n\n"
            f"Добавлено: {added_count} товаров\n\n"
            "Теперь нажмите «Подтвердить» для завершения:",
            parse_mode="HTML",
            reply_markup=get_confirm_reply_keyboard()
        )

    except Exception as e:
        logger.error(f"Ошибка добавления всех товаров: {e}")
        await message.answer(f"❌ Ошибка: {e}", parse_mode="HTML")
    finally:
        await client.close()


# --- Обработчик: Подтверждение поставки (reply-кнопка) ---

@router.message(F.text == "✅ Подтвердить поставку")
async def msg_confirm_supply(message: Message):
    """Подтвердить поставку и сгенерировать стикеры."""
    user_id = message.from_user.id

    if user_id not in processing_data or processing_data[user_id].get("pending_create", True):
        await message.answer("❌ Нет активной поставки. Сначала проверьте заказы.")
        return

    data = processing_data[user_id]
    supply_id = data.get("supply_id")
    order_ids = data.get("order_ids", [])

    if not supply_id:
        await message.answer("❌ Нет поставки для подтверждения.")
        return

    storage = get_storage()
    api_key = storage.get_api_key(user_id)

    if not api_key:
        await message.answer("❌ API-ключ не найден.")
        return

    await message.answer(
        "⏳ <b>Подтверждаю поставку и генерирую стикеры...</b>\n"
        "Это может занять некоторое время.",
        parse_mode="HTML"
    )

    client = WBApiClient(api_key)
    try:
        # 1. Подтверждаем поставку
        await client.confirm_supply(supply_id)
        logger.info(f"Поставка {supply_id} подтверждена")

        # 2. Получаем список заказов в поставке
        supply_orders = await client.get_supply_orders(supply_id)
        order_ids_api = [o.get("orderId", o.get("id")) for o in supply_orders if o.get("orderId") or o.get("id")]

        if not order_ids_api:
            order_ids_api = order_ids

        await message.answer("⏳ Получаю стикеры заказов...")

        # 3. Получаем стикеры
        stickers_data = await client.get_orders_stickers(order_ids_api, sticker_type="png")

        # 4. Генерируем QR-код поставки
        qr_bytes = sticker_gen.generate_qr_code(supply_id)
        supply_sticker_img = sticker_gen.create_supply_sticker_image(supply_id, qr_bytes)

        pdf_images = [supply_sticker_img]

        for sticker_item in stickers_data:
            decoded = WBApiClient.decode_sticker_file(sticker_item)
            if decoded:
                pdf_images.append(decoded)

        if not stickers_data:
            for o in supply_orders:
                barcode = o.get("barcode") or o.get("skus", [None])[0] or str(order_ids_api[0])
                bc_bytes = sticker_gen.generate_barcode(barcode, o.get("orderId", 0))
                sticker_img = sticker_gen.create_order_sticker_image(
                    o.get("orderId", 0), barcode, bc_bytes
                )
                pdf_images.append(sticker_img)

        # 5. Создаём PDF
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"supply_{supply_id}_{timestamp}.pdf"
        pdf_path = os.path.join(STICKERS_DIR, pdf_filename)
        sticker_gen.create_pdf_from_images(pdf_images, pdf_path)

        # 6. Отправляем PDF
        await message.answer(
            "✅ <b>Поставка успешно создана и подтверждена!</b>\n\n"
            f"📦 ID поставки: <code>{supply_id}</code>\n"
            f"📄 Сгенерирован PDF со стикерами.\n\n"
            "Отправляю файл..."
        )

        pdf_file = FSInputFile(pdf_path)
        await message.answer_document(
            pdf_file,
            caption=(
                f"📦 <b>Стикеры поставки {supply_id}</b>\n"
                f"Заказов в поставке: {len(order_ids_api)}\n"
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
            parse_mode="HTML"
        )

        # 7. Очищаем данные
        if user_id in processing_data:
            del processing_data[user_id]

        user_data = storage.get_user(user_id)
        user_data.supply_id = None
        user_data.current_order_id = None
        user_data.confirmed = True
        storage.save_user(user_id, user_data)

        # 8. Главное меню
        await message.answer(
            "✅ <b>Готово!</b>\n\n"
            "Я продолжаю отслеживать новые заказы.",
            parse_mode="HTML",
            reply_markup=get_main_reply_keyboard()
        )

    except ConflictError as e:
        await message.answer(
            f"❌ <b>Ошибка 409.</b> Поставка уже подтверждена или имеет неверный статус.\n\n"
            f"{e.body[:300] if e.body else ''}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка подтверждения поставки: {e}")
        await message.answer(
            f"❌ <b>Ошибка при подтверждении:</b> {e}",
            parse_mode="HTML"
        )
    finally:
        await client.close()


# --- Обработчик: Пропустить заказ ---

@router.message(F.text == "❌ Пропустить")
async def msg_skip_order(message: Message):
    """Пропустить заказ."""
    user_id = message.from_user.id
    if user_id in processing_data:
        del processing_data[user_id]

    await message.answer(
        "⏭ Заказ пропущен.\n"
        "Я продолжу отслеживать новые заказы.",
        reply_markup=get_main_reply_keyboard()
    )


# --- Обработчик: Отмена поставки ---

@router.message(F.text == "❌ Отменить")
async def msg_cancel_supply(message: Message):
    """Отменить создание поставки."""
    user_id = message.from_user.id

    if user_id in processing_data:
        del processing_data[user_id]

    await message.answer(
        "❌ Создание поставки отменено.\n\n"
        "Я продолжу отслеживать новые заказы.",
        reply_markup=get_main_reply_keyboard()
    )


# --- Обработчик: Статус подключения (reply-кнопка) ---

@router.message(F.text == "📊 Статус подключения")
async def msg_status(message: Message):
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
                parse_mode="HTML",
                reply_markup=get_main_reply_keyboard()
            )
        else:
            await message.answer(
                "❌ <b>Не удалось подключиться к API.</b>\n"
                "Возможно, ключ недействителен или истёк.",
                parse_mode="HTML",
                reply_markup=get_main_reply_keyboard()
            )
    finally:
        await client.close()