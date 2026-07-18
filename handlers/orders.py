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
)
from keyboards.reply import (
    get_main_reply_keyboard,
    remove_keyboard,
)
from utils.stickers import StickerGenerator

logger = logging.getLogger(__name__)

router = Router()
sticker_gen = StickerGenerator(output_dir=STICKERS_DIR)


# Временные данные пользователя
# {user_id: {"order_ids": list[int], "selected_orders": set[int], "items": list[dict],
#            "trbx_list": list[dict], "supply_id": Optional[str], "order_details": dict}}
user_sessions: dict[int, dict] = {}


async def get_order_details(client, items):
    nm_ids = [i.get("nmId") for i in items if i.get("nmId")]
    if not nm_ids:
        return {}
    try:
        details = await client.get_orders_status(nm_ids)
        return {d.get("nmId"): d for d in details if d.get("nmId")}
    except Exception as e:
        logger.warning(f"Ошибка получения деталей: {e}")
        return {}


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
            if info.get("phone"): parts.append(f"📞 {info['phone']}")
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
        await message.answer("❌ Сначала установите API-ключ через /start или /set_key", parse_mode="HTML")
        return

    await message.answer("🔍 Проверяю новые заказы...")
    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        if orders:
            order_details = await get_order_details(client, orders)
            new_cnt = 0
            for order in orders:
                oid = order.get("id")
                if not oid: continue
                ud = storage.get_user(user_id)
                if oid in ud.added_order_ids or oid in ud.notified_order_ids: continue
                new_cnt += 1
                nm = order.get("nmId")
                d = order_details.get(nm, {})
                await message.answer(
                    f"🆕 <b>Новый заказ!</b>\n\n"
                    f"📦 ID заказа: <code>{oid}</code>\n"
                    f"🔖 Название: {d.get('subject','—')}\n"
                    f"🎨 Цвет: {d.get('color','—')}\n"
                    f"📄 Артикул: {d.get('supplierArticle','—')}\n"
                    f"💰 Цена: {order.get('totalPrice','—')} ₽\n\n"
                    "Создайте поставку.\n\n"
                    "🔙 <i>Главное меню</i>",
                    parse_mode="HTML",
                    reply_markup=get_create_supply_keyboard(oid)
                )
                ud.notified_order_ids.append(oid)
                storage.save_user(user_id, ud)
                user_sessions.setdefault(user_id, {}).setdefault("order_ids", []).append(oid)
            if new_cnt == 0:
                await message.answer("✅ Новых заказов нет.", reply_markup=get_main_reply_keyboard())
        else:
            await message.answer("✅ Новых заказов нет.", reply_markup=get_main_reply_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
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
        await callback.message.answer("❌ API-ключ не найден.")
        return

    client = WBApiClient(api_key)
    try:
        orders = await client.get_new_orders()
        items = []
        for o in orders:
            if o.get("id"):
                ud = storage.get_user(user_id)
                if not (o["id"] in ud.added_order_ids or o["id"] in ud.notified_order_ids):
                    items.append(o)
        if not items:
            await callback.message.answer("❌ Нет новых заказов.")
            return

        order_details = await get_order_details(client, items)
        user_sessions[user_id] = {
            "order_ids": [i["id"] for i in items],
            "selected_orders": set(),
            "items": items,
            "trbx_list": [],
            "order_details": order_details,
        }

        text = "📋 <b>Выберите товары:</b>\n\n"
        for item in items:
            d = order_details.get(item.get("nmId"), {})
            text += f"⬜ {d.get('subject','—')} | {d.get('color','—')} | Арт: {d.get('supplierArticle','—')} | {item.get('totalPrice','—')} ₽\n"

        await callback.message.answer(text, parse_mode="HTML",
            reply_markup=get_order_items_keyboard(items, order_details, set()))
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
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
        d = s["order_details"].get(item.get("nmId"), {})
        cb = "✅" if item["id"] in s["selected_orders"] else "⬜"
        text += f"{cb} {d.get('subject','—')} | {d.get('color','—')} | Арт: {d.get('supplierArticle','—')} | {item.get('totalPrice','—')} ₽\n"
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
        await callback.message.answer("❌ Выберите хотя бы один товар.")
        return

    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key: return

    await callback.message.answer("⏳ Создаю поставку...")

    client = WBApiClient(api_key)
    try:
        supply_data = await client.create_supply()
        supply_id = supply_data.get("id")
        if not supply_id:
            await callback.message.answer("❌ Ошибка: не получен ID поставки.")
            return

        selected_ids = list(s["selected_orders"])
        await client.add_orders_to_supply(supply_id, selected_ids)
        s["supply_id"] = supply_id

        barcode_bytes = await client.get_supply_barcode(supply_id)
        client_info = await format_client_info(client, selected_ids[0]) if selected_ids else ""

        supply_text = f"✅ <b>Поставка создана!</b>\n\n📦 ID: <code>{supply_id}</code>\n📦 Товаров: {len(selected_ids)}\n\n"
        for item in s["items"]:
            if item["id"] in s["selected_orders"]:
                d = s["order_details"].get(item.get("nmId"), {})
                supply_text += f"• {d.get('subject','—')} — {item.get('totalPrice','—')} ₽\n"

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
        await callback.message.answer(f"❌ Ошибка: {e}")
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
        await callback.message.answer(f"❌ Ошибка: {e}")
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
        await callback.message.answer(f"❌ Ошибка: {e}")
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
        await callback.message.answer("❌ Создайте хотя бы одно грузоместо.")
        return

    await callback.message.answer("⏳ Получаю стикеры...")

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
            "✅ <b>Поставка завершена!</b>\n\nЯ продолжаю отслеживать новые заказы.",
            parse_mode="HTML", reply_markup=get_main_reply_keyboard()
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    finally:
        await client.close()


@router.callback_query(F.data.startswith("skip_order:"))
async def cb_skip_order(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("⏭ Заказ пропущен.", reply_markup=get_main_reply_keyboard())


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
    await _check_orders_logic(message)


@router.message(F.text == "❌ Пропустить")
async def msg_skip_order(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions: del user_sessions[user_id]
    await message.answer("⏭ Заказ пропущен.", reply_markup=get_main_reply_keyboard())


@router.message(F.text == "❌ Отменить")
async def msg_cancel_supply(message: Message):
    user_id = message.from_user.id
    if user_id in user_sessions: del user_sessions[user_id]
    await message.answer("❌ Отменено.", reply_markup=get_main_reply_keyboard())


@router.message(F.text == "📊 Статус подключения")
async def msg_status(message: Message):
    user_id = message.from_user.id
    storage = get_storage()
    api_key = storage.get_api_key(user_id)
    if not api_key:
        await message.answer("❌ API-ключ не установлен.", parse_mode="HTML")
        return
    client = WBApiClient(api_key)
    try:
        valid = await client.check_auth()
        await message.answer(
            "✅ <b>Подключение активно.</b>" if valid else "❌ <b>Не удалось подключиться.</b>",
            parse_mode="HTML", reply_markup=get_main_reply_keyboard()
        )
    finally:
        await client.close()