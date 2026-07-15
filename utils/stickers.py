import io
import logging
import os
from typing import Optional

import qrcode
from PIL import Image, ImageDraw, ImageFont
from barcode import Code128
from barcode.writer import ImageWriter

logger = logging.getLogger(__name__)


class StickerGenerator:
    """Генерация QR-кодов поставки, штрих-кодов заказов и объединение в PDF."""

    def __init__(self, output_dir: str = "data/stickers"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        # Попытка загрузить шрифт для подписей
        try:
            self.font = ImageFont.truetype("arial.ttf", 20)
            self.font_small = ImageFont.truetype("arial.ttf", 14)
        except (IOError, OSError):
            self.font = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

    def generate_qr_code(self, supply_id: str) -> bytes:
        """Сгенерировать QR-код для поставки."""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        # В Wildberries QR-код поставки обычно содержит ID поставки
        qr.add_data(supply_id)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()

    def generate_barcode(self, barcode_value: str, order_id: int) -> bytes:
        """Сгенерировать штрих-код для заказа (Code128)."""
        buf = io.BytesIO()
        try:
            # Code128 поддерживает цифры и буквы
            rv = Code128(barcode_value, writer=ImageWriter())
            rv.write(buf, options={
                "module_width": 0.4,
                "module_height": 15.0,
                "font_size": 10,
                "text_distance": 3.0,
                "quiet_zone": 2.0,
            })
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать штрих-код для заказа {order_id}: {e}")
            # Создаём заглушку
            img = Image.new("RGB", (300, 100), "white")
            draw = ImageDraw.Draw(img)
            draw.text((10, 40), f"Barcode: {barcode_value}", fill="black", font=self.font_small)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.getvalue()

        buf.seek(0)
        return buf.getvalue()

    def create_supply_sticker_image(self, supply_id: str, qr_bytes: bytes) -> bytes:
        """
        Создать изображение стикера поставки с QR-кодом и подписью.
        Возвращает PNG-байты.
        """
        qr_img = Image.open(io.BytesIO(qr_bytes))

        # Создаём полотно с местом для подписи
        padding = 20
        label_height = 40
        width = qr_img.width + padding * 2
        height = qr_img.height + label_height + padding * 2

        sticker = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sticker)

        # Вставляем QR-код
        sticker.paste(qr_img, (padding, padding))

        # Подпись
        text = f"Поставка: {supply_id[:16]}..."
        draw.text((padding, qr_img.height + padding + 5), text, fill="black", font=self.font)

        buf = io.BytesIO()
        sticker.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()

    def create_order_sticker_image(
        self,
        order_id: int,
        barcode_value: str,
        barcode_bytes: bytes
    ) -> bytes:
        """
        Создать изображение стикера заказа со штрих-кодом.
        Возвращает PNG-байты.
        """
        bc_img = Image.open(io.BytesIO(barcode_bytes))

        padding = 20
        label_height = 40
        width = max(bc_img.width + padding * 2, 300)
        height = bc_img.height + label_height + padding * 2

        sticker = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sticker)

        # Вставляем штрих-код
        x_offset = (width - bc_img.width) // 2
        sticker.paste(bc_img, (x_offset, padding))

        # Подпись — ID заказа
        text = f"Заказ #{order_id}"
        draw.text((padding, bc_img.height + padding + 5), text, fill="black", font=self.font)

        buf = io.BytesIO()
        sticker.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()

    def create_pdf_from_images(
        self,
        images: list[bytes],
        output_path: str
    ) -> str:
        """
        Объединить несколько PNG-изображений в один PDF-файл.
        Каждая картинка на отдельной странице.

        Args:
            images: список PNG-байтов
            output_path: путь для сохранения PDF

        Returns:
            путь к сохранённому PDF
        """
        if not images:
            raise ValueError("Нет изображений для создания PDF")

        img_list = []
        for img_bytes in images:
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img_list.append(img)

        if img_list:
            first_img = img_list[0]
            rest_imgs = img_list[1:] if len(img_list) > 1 else None
            first_img.save(
                output_path,
                save_all=True,
                append_images=rest_imgs,
                format="PDF",
                resolution=300.0,
            )

        logger.info(f"PDF сохранён: {output_path}")
        return output_path

    def save_sticker_file(self, sticker_bytes: bytes, filename: str) -> str:
        """Сохранить файл стикера в директорию вывода."""
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "wb") as f:
            f.write(sticker_bytes)
        return filepath