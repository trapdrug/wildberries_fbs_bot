import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, FSInputFile, CallbackQuery

from config import POLL_INTERVAL, STICKERS_DIR
from storage.user_storage import get_storage
from wb_api import (
    WBApiClient,
    AuthError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    WildberriesAPIError,
)
from keyboards.inline import (
    get_create_supply_keyboard,
    get_order_items_keyboard,
    get_trbx_keyboard,
    get_check_orders_keyboard,
)
from keyboards.reply import (
    get_main_reply_keyboard,
    remove_keyboard,
)
from utils.stickers import StickerGenerator

logger = logging.getLogger(__name__)

router = Router()
sticker_gen = StickerGenerator(output_dir=STICKERS_DIR)

# Unicode characters for messages
EM_DASH = "\u2014"  # —


# Временные данные пользователя
# {user_id: {"order_ids": list[int], "selected_orders": set[int], "items": list[dict],
#            "trbx_list": list[dict], "supply_id": Optional[str], "order_details": dict}}
user_sessions: dict[int, dict] = {}


def get_price(item: dict) -> int:
    """Получить цену из заказа (поддерживает разные форматы API)."""
    # Приоритет: finalPrice > price > totalPrice > 0
    return item.get("finalPrice") or item.get("price") or item.get("totalPrice") or 0


def format_price(price: int) -> str:
    """Форматировать цену: если в копейках (целое число >= 100) — перевести в рубли."""
    if price is None:
        return "0"
    # Цена в копейках (обычно 51200 = 512.00 руб)
    if price >= 100:
        return f"{price / 100:.2f}".rstrip('0').rstrip('.')
    return str(price)


def get_order_name(order: dict) -> str:
    """Return the product name from the FBS new-orders response."""
    return order.get("article") or EM_DASH


def get_order_article(order: dict) -> str:
    """Use a separate seller article when the account provides one."""
    return (
        order.get("supplierArticle")
        or order.get("vendorCode")
        or (order.get("skus") or [None])[0]
        or order.get("article")
        or EM_DASH
    )


def format_order_message(order: dict, order_details: dict) -> str:
    """Форматировать сообщение с информацией о заказе."""
    oid = order.get("id")
    name = get_order_name(order)
    color = order.get("colorCode") or EM_DASH
    article = get_order_article(order)
    price = get_price(order)
    
    return (
        f"🆕 <b>Новый заказ!</b>\n\n"
        f"📦 ID заказа: <code>{oid}</code>\n"
        f"🏷️ Название: {name}\n"
        f"🎨 Цвет: {color}\n"
        f"📄 Артикул: {article}\n"
        f"💰 Цена: {format_price(price)} ₽\n\n"
        f"Создайте поставку.\n\n"
        f"🔙 <i>Главное меню</i>"
    )


async def get_order_details(client, items):
    """
    Получить детали товаров из заказов.
    API /api/v3/orders/new возвращает: article (артикул продавца), colorCode (цвет)
    Название товара (subject) получаем через content API
    """
    result = {}
    for item in items:
        nm = item.get("nmId")
        if nm:
            # Базовые данные из заказа
            result[nm] = {
                "nmId": nm,
                "subject": EM_DASH,  # Будет заполнено из content API
                "color": item.get("colorCode") or EM_DASH,
                "supplierArticle": item.get("article") or EM_DASH,
                "price": get_price(item),
            }

    # Получаем названия товаров через content API
    nm_ids = list(result.keys())
    if nm_ids:
        try:
            cards_data = await client.get_cards_list(nm_ids)
            # Try to get cards from different possible structures
            cards_list = []
            if isinstance(cards_data, dict):
                # Option 1: top-level "cards"
                cards_list = cards_data.get("cards", [])
                # Option 2: nested under "data"
                if not cards_list and "data" in cards_data:
                    cards_list = cards_data["data"].get("cards", [])
            elif isinstance(cards_data, list):
                # Option 3: maybe the root is a list?
                cards_list = cards_data
            for card in cards_list:
                nm_id = card.get("nmID")
                if nm_id and nm_id in result:
                    # Название товара берем из карточки
                    result[nm_id]["subject"] = card.get("title") or card.get("name") or EM_DASH
        except Exception as e:
            logger.warning(f"Ошибка получения карточек товаров: {e}")

    return result


async def format_client_info(client, order_id):
    try:
        data = await client.get_orders_client([order_id])
        if data and isinstance(data, dict):
            items = data.get("data", [])
        else:
            items = data if isinstance(data, list) else []
        if items:
            info = items[0]
            parts = []
            if info.get("name"): parts.append(f"👤 {info['name']}")
            if info.get("phone"): parts.append(f"📱 {info['phone']}")
            if info.get("address"): parts.append(f"📍 {info['address']}")
            if parts: return "\n".join(parts)
    except Exception as e:
        logger.warning(f"Ошибка клиента: {e}")
    return ""


async def _check_orders_logic(message):
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key:
        await message.answer("🚫 Сначала установите API-ключ через /start или /set_key", parse_mode="HTML")
        return

    await message.answer("🔍 Проверяю заказы...")
    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        if orders:
            order_details = await get_order_details(client, orders)
            new_cnt = 0
            shown_cnt = 0
            for order in orders:
                oid = order.get("id")
                if not oid: continue
                ud = storage.get_user(user_id)
                is_new = oid not in ud.added_order_ids and oid not in ud.notified_order_ids
                if is_new:
                    new_cnt += 1
                shown_cnt += 1
                
                # Форматируем сообщение с данными из заказа
                text = format_order_message(order, order_details)
                
                # Показываем кнопку "Создать поставку" для ВСЕХ заказов, которые ещё не в поставке
                if oid not in ud.added_order_ids:
                    await message.answer(
                        text,
                        parse_mode="HTML",
                        reply_markup=get_create_supply_keyboard(oid)
                    )
                else:
                    await message.answer(
                        text,
                        parse_mode="HTML"
                    )
                    
                if is_new:
                    ud.notified_order_ids.append(oid)
                    storage.save_user(user_id, ud)
                    user_sessions.setdefault(user_id, {}).setdefault("order_ids", []).append(oid)

            if new_cnt == 0 and shown_cnt > 0:
                await message.answer(
                    f"📦 Всего заказов: {shown_cnt} (все уже были показаны ранее)",
                    reply_markup=get_main_reply_keyboard()
                )
            elif new_cnt > 0:
                await message.answer(
                    f"🌵 Проверка завершена.\n🌵 Новых: {new_cnt}\n📦 Всего: {shown_cnt}",
                    reply_markup=get_main_reply_keyboard()
                )
        else:
            await message.answer("🌵 Новых заказов нет.", reply_markup=get_main_reply_keyboard())
    except Exception as e:
        await message.answer(f"🚫 Ошибка: {e}")
        logger.error(f"Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("create_supply:"))
async def cb_create_supply(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key:
        await callback.message.answer("🚫 API-ключ не найден.")
        return

    # Делаем СВЕЖИЙ запрос к API — не используем кешированные данные
    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        if not orders:
            await callback.message.answer("🚫 Нет новых заказов для создания поставки.")
            return

        # Получаем детали заказов
        order_details = await get_order_details(client, orders)
        
        # Сохраняем в сессию ВСЕ свежие заказы
        user_sessions[user_id] = {
            "order_ids": [o["id"] for o in orders if o.get("id")],
            "selected_orders": set(),
            "items": orders,
            "trbx_list": [],
            "order_details": order_details,
        }

        text = "📋 <b>Выберите товары для поставки:</b>\n\n"
        for item in orders:
            price = get_price(item)
            article = item.get("article") or EM_DASH
            text += f"🆔 Артикул: {article} | {format_price(price)} ₽\n"

        await callback.message.answer(text, parse_mode="HTML",
            reply_markup=get_order_items_keyboard(orders, order_details, set()))
    except Exception as e:
        await callback.message.answer(f"🚫 Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("toggle_item:"))
async def cb_toggle_item(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_sessions: return
    try:
        oid = int(callback.data.split(":")[1])
    except: return
    s = user_sessions[user_id]
    if oid in s["selected_orders"]: s["selected_orders"].discard(oid)
    else: s["selected_orders"].add(oid)

    text = "📋 <b>Выберите товары:</b>\n\n"
    for item in s["items"]:
        cb = "🌵 " if item["id"] in s["selected_orders"] else "🆔 "
        price = get_price(item)
        article = item.get("article") or EM_DASH
        text += f"{cb} Артикул: {article} | {format_price(price)} ₽\n"
    try:
        await callback.message.edit_text(text, parse_mode="HTML",
            reply_markup=get_order_items_keyboard(s["items"], s["order_details"], s["selected_orders"]))
    except: pass


@router.callback_query(F.data == "next_to_trbx")
async def cb_next_to_trbx(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_sessions: return
    s = user_sessions[user_id]
    if not s["selected_orders"]:
        await callback.message.answer("🚫 Выберите хотя бы один товар.")
        return

    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key: return

    await callback.message.answer("⏱ Создаю поставку...")

    client = WBApiClient(api_key)
    try:
        # The selection screen can be open for a while.  Refresh the endpoint
        # immediately before creating the supply so we never add stale orders.
        fresh_orders = await client.get_new_orders()
        fresh_order_ids = {order.get("id") for order in fresh_orders}
        selected_ids = [
            order_id for order_id in s["selected_orders"]
            if order_id in fresh_order_ids
        ]
        if not selected_ids:
            await callback.message.answer(
                "Нет новых заказов для создания поставки. Обновите список заказов."
            )
            return

        s["items"] = fresh_orders
        supply_name = f"Поставка от {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        supply_data = await client.create_supply(supply_name)
        supply_id = supply_data.get("id")
        if not supply_id:
            await callback.message.answer("🚫 Ошибка: не получен ID поставки.")
            return

        await client.add_orders_to_supply(supply_id, selected_ids)
        s["supply_id"] = supply_id

        # WB makes the supply QR code available only after the supply has been
        # transferred to delivery. A missing QR must not hide a successful
        # supply creation or turn it into a user-facing error.
        try:
            barcode_bytes = await client.get_supply_barcode(supply_id)
        except WildberriesAPIError as error:
            logger.info(
                "Supply %s created without QR code yet: %s", supply_id, error
            )
            barcode_bytes = None
        client_info = await format_client_info(client, selected_ids[0]) if selected_ids else ""

        supply_text = f"🌵 <b>Поставка создана!</b>\n\n📦 ID: <code>{supply_id}</code>\n📦 Товаров: {len(selected_ids)}\n\n"
        for item in s["items"]:
            if item["id"] in s["selected_orders"]:
                price = get_price(item)
                article = item.get("article") or EM_DASH
                # Convert kopecks to rubles (1 ruble = 100 kopecks)
                price_rubles = price / 100 if price >= 100 else price
                supply_text += f"📦 {article} — {price_rubles} ₽\n"

        if client_info:
            supply_text += f"\n<b>Информация о клиенте:</b>\n{client_info}\n"

        try:
            await callback.message.edit_text(supply_text, parse_mode="HTML")
        except: pass

        if barcode_bytes:
            qr_path = os.path.join(STICKERS_DIR, f"qr_{supply_id}.png")
            with open(qr_path, "wb") as f:
                f.write(barcode_bytes)
            await callback.message.answer_document(FSInputFile(qr_path), caption=f"📱 QR-код поставки {supply_id}")

        trbx_list = s.get("trbx_list", [])
        await callback.message.answer(
            f"📦 <b>Грузоместа</b>\nСоздано: {len(trbx_list)}\n\nДобавьте грузоместа и нажмите Завершить.",
            parse_mode="HTML",
            reply_markup=get_trbx_keyboard(supply_id, trbx_list)
        )
    except Exception as e:
        await callback.message.answer(f"🚫 Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("add_trbx:"))
async def cb_add_trbx(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_sessions or not user_sessions[user_id].get("supply_id"): return
    s = user_sessions[user_id]
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key: return

    client = WBApiClient(api_key)
    try:
        result = await client.create_trbx(s["supply_id"])
        trbx_id = result.get("id")
        if trbx_id:
            s.setdefault("trbx_list", []).append({"id": trbx_id, "number": len(s["trbx_list"]) + 1})
        await callback.message.edit_text(
            f"📦 <b>Грузоместа</b>\nСоздано: {len(s['trbx_list'])}\n",
            parse_mode="HTML",
            reply_markup=get_trbx_keyboard(s["supply_id"], s["trbx_list"])
        )
    except Exception as e:
        await callback.message.answer(f"🚫 Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("del_trbx:"))
async def cb_del_trbx(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_sessions: return
    try:
        trbx_id = callback.data.split(":", 1)[1]
    except: return
    s = user_sessions[user_id]
    if not s.get("supply_id"): return

    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key: return

    client = WBApiClient(api_key)
    try:
        await client.delete_trbx(s["supply_id"], trbx_id)
        s["trbx_list"] = [t for t in s.get("trbx_list", []) if t.get("id") != trbx_id]
        await callback.message.edit_text(
            f"📦 <b>Грузоместа</b>\nСоздано: {len(s['trbx_list'])}\n",
            parse_mode="HTML",
            reply_markup=get_trbx_keyboard(s["supply_id"], s["trbx_list"])
        )
    except Exception as e:
        await callback.message.answer(f"🚫 Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data == "finish_supply")
async def cb_finish_supply(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if user_id not in user_sessions: return
    s = user_sessions[user_id]
    supply_id = s.get("supply_id")
    trbx_list = s.get("trbx_list", [])
    if not supply_id: return
    if not trbx_list:
        await callback.message.answer("🚫 Создайте хотя бы одно грузоместо.")
        return

    await callback.message.answer("🔍 Получаю стикеры...")

    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    client = WBApiClient(api_key)
    try:
        trbx_ids = [t["id"] for t in trbx_list]
        stickers = await client.get_trbx_stickers(supply_id, trbx_ids)
        if stickers:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_path = os.path.join(STICKERS_DIR, f"supply_{supply_id}_{ts}.pdf")
            sticker_gen.create_pdf_from_images(stickers, pdf_path)
            await callback.message.answer_document(
                FSInputFile(pdf_path),
                caption=f"📦 Стикеры поставки {supply_id}\nГрузомест: {len(trbx_ids)}\nТоваров: {len(s['selected_orders'])}"
            )

        ud = storage.get_user(user_id)
        for oid in s.get("selected_orders", []):
            if oid not in ud.added_order_ids: ud.added_order_ids.append(oid)
        ud.confirmed = True
        storage.save_user(user_id, ud)
        del user_sessions[user_id]

        await callback.message.answer(
            "🌵 <b>Поставка завершена!</b>\n\nЯ продолжаю отслеживать новые заказы.",
            parse_mode="HTML", reply_markup=get_main_reply_keyboard()
        )
    except Exception as e:
        await callback.message.answer(f"🚫 Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("skip_order:"))
async def cb_skip_order(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("⏩ Заказ пропущен.", reply_markup=get_main_reply_keyboard())


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "check_orders")
async def cb_check_orders(callback: CallbackQuery):
    await callback.answer()
    await _check_orders_logic(callback.message)


@router.message(F.text == "🔍 Проверить заказы")
async def msg_check_orders_button(message: Message):
    await _check_orders_logic(message)


@router.message(F.text == "📦 Создать поставку")
async def msg_create_supply(message: Message):
    """Создать поставку из всех новых заказов (reply-кнопка)."""
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key:
        await message.answer("🚫 Сначала установите API-ключ через /start или /set_key", parse_mode="HTML")
        return

    await message.answer("🔍 Проверяю новые заказы для создания поставки...")
    
    # Делаем СВЕЖИЙ запрос к API
    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        if not orders:
            await message.answer("🚫 Нет новых заказов для создания поставки.", reply_markup=get_main_reply_keyboard())
            return

        # Получаем детали заказов
        order_details = await get_order_details(client, orders)
        
        # Сохраняем в сессию
        user_sessions[user_id] = {
            "order_ids": [o["id"] for o in orders if o.get("id")],
            "selected_orders": set(),
            "items": orders,
            "trbx_list": [],
            "order_details": order_details,
        }

        text = "📋 <b>Выберите товары для поставки:</b>\n\n"
        for item in orders:
            price = get_price(item)
            article = item.get("article") or EM_DASH
            text += f"🆔 Артикул: {article} | {format_price(price)} ₽\n"

        await message.answer(text, parse_mode="HTML",
            reply_markup=get_order_items_keyboard(orders, order_details, set()))
    except Exception as e:
        await message.answer(f"🚫 Ошибка: {e}")
        logger.error(f"Ошибка создания поставки: {e}")
    finally:
        await client.close()


@router.message(F.text == "❌ Пропустить")
async def msg_skip_order(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions: del user_sessions[user_id]
    await message.answer("⏩ Заказ пропущен.", reply_markup=get_main_reply_keyboard())


@router.message(F.text == "❌ Отменить")
async def msg_cancel_supply(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions: del user_sessions[user_id]
    await message.answer("🚫 Отменено.", reply_markup=get_main_reply_keyboard())


@router.message(F.text == "📊 Статус подключения")
async def msg_status(message: Message):
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key:
        await message.answer("🚫 API-ключ не установлен.", parse_mode="HTML")
        return
    client = WBApiClient(api_key)
    try:
        valid = await client.check_auth()
        await message.answer(
            "🌵 <b>Подключение активно.</b>" if valid else "🚫 <b>Не удалось подключиться.</b>",
            parse_mode="HTML", reply_markup=get_main_reply_keyboard()
        )
    finally:
        await client.close()
