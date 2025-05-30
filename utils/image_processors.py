# utils/image_processors.py
import logging
import random
from io import BytesIO

# Conditional import for Pillow components
try:
    from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

    PILLOW_AVAILABLE = True
except ImportError:
    Image, ImageDraw, ImageFont, UnidentifiedImageError = (
        None,
        None,
        None,
        None,
    )
    PILLOW_AVAILABLE = False

from aiogram.types import BufferedInputFile
from settings import (
    STROOP_COLORS_DEF,
    STROOP_FONT_PATH,
    STROOP_IMAGE_SIZE,
    STROOP_TEXT_COLOR_ON_PATCH,
    MR_COLLAGE_CELL_SIZE,
    MR_COLLAGE_BG_COLOR,
)

logger = logging.getLogger(__name__)


def _get_font(font_path: str, size: int):
    if not PILLOW_AVAILABLE or not ImageFont:
        return None
    try:
        return ImageFont.truetype(font_path, size)
    except IOError:
        logger.warning(
            f"Шрифт '{font_path}' не найден. Используется шрифт Pillow по умолчанию."
        )
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            logger.warning(
                f"Не удалось загрузить шрифт по умолчанию Pillow с размером {size}. "
                "Загрузка со стандартным размером."
            )
            return ImageFont.load_default()
        except Exception as e_def:
            logger.error(
                f"Ошибка при загрузке шрифта Pillow по умолчанию: {e_def}"
            )
            return None
    except Exception as e_generic:
        logger.error(
            f"Общая ошибка при загрузке шрифта {font_path}: {e_generic}"
        )
        return None


def _generate_stroop_part2_image(
    patch_color_name: str, text_on_patch_name: str
) -> BufferedInputFile | None:
    if not PILLOW_AVAILABLE:
        logger.warning(
            "Stroop P2: Pillow недоступен, изображение не будет сгенерировано."
        )
        return None

    patch_rgb = STROOP_COLORS_DEF[patch_color_name]["rgb"]
    text_rgb = STROOP_TEXT_COLOR_ON_PATCH
    img = Image.new("RGB", STROOP_IMAGE_SIZE, color=patch_rgb)
    draw = ImageDraw.Draw(img)
    font = _get_font(STROOP_FONT_PATH, 40)
    text_to_draw = STROOP_COLORS_DEF[text_on_patch_name]["name"]

    if font:
        try:
            # Preferred method for text dimensions
            bbox = draw.textbbox((0, 0), text_to_draw, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception as e_text_measure:
            logger.error(
                f"Stroop P2: Ошибка измерения текста ('{text_to_draw}') через textbbox: {e_text_measure}. "
                "Используется оценка."
            )
            # Fallback to rough estimate if text measurement fails
            tw, th = (
                STROOP_IMAGE_SIZE[0] * 0.8,
                STROOP_IMAGE_SIZE[1] * 0.5,
            )

        x = (STROOP_IMAGE_SIZE[0] - tw) / 2
        y = (STROOP_IMAGE_SIZE[1] - th) / 2
        draw.text((x, y), text_to_draw, fill=text_rgb, font=font)
    else:
        draw.text((10, 10), "Font Error", fill=text_rgb)
        logger.error("Stroop P2: Не удалось загрузить шрифт.")

    bio = BytesIO()
    bio.name = f"s_p2_{patch_color_name}_{text_on_patch_name}.png"
    try:
        img.save(bio, "PNG")
        bio.seek(0)
        return BufferedInputFile(bio.read(), filename=bio.name)
    except Exception as e_save:
        logger.error(f"Stroop P2: Ошибка сохранения изображения: {e_save}")
        return None


def _generate_stroop_part3_image(
    word_name: str, ink_name: str
) -> BufferedInputFile | None:
    if not PILLOW_AVAILABLE:
        logger.warning(
            "Stroop P3: Pillow недоступен, изображение не будет сгенерировано."
        )
        return None

    ink_rgb = STROOP_COLORS_DEF[ink_name]["rgb"]
    bg_rgb = (255, 255, 255)
    img = Image.new("RGB", STROOP_IMAGE_SIZE, color=bg_rgb)
    draw = ImageDraw.Draw(img)
    font = _get_font(STROOP_FONT_PATH, 50)
    text_to_draw = STROOP_COLORS_DEF[word_name]["name"]

    if font:
        try:
            bbox = draw.textbbox((0, 0), text_to_draw, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception as e_text_measure:
            logger.error(
                f"Stroop P3: Ошибка измерения текста ('{text_to_draw}') через textbbox: {e_text_measure}. "
                "Используется оценка."
            )
            tw, th = (
                STROOP_IMAGE_SIZE[0] * 0.8,
                STROOP_IMAGE_SIZE[1] * 0.5,
            )

        x = (STROOP_IMAGE_SIZE[0] - tw) / 2
        y = (STROOP_IMAGE_SIZE[1] - th) / 2
        stroke_width = 1 if ink_name == "Желтый" else 0
        stroke_fill = (100, 100, 100) if stroke_width > 0 else None

        draw.text(
            (x, y),
            text_to_draw,
            fill=ink_rgb,
            font=font,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
    else:
        draw.text((10, 10), "Font Error", fill=ink_rgb)
        logger.error("Stroop P3: Не удалось загрузить шрифт.")

    bio = BytesIO()
    bio.name = f"s_p3_{word_name}_{ink_name}.png"
    try:
        img.save(bio, "PNG")
        bio.seek(0)
        return BufferedInputFile(bio.read(), filename=bio.name)
    except Exception as e_save:
        logger.error(f"Stroop P3: Ошибка сохранения изображения: {e_save}")
        return None


async def generate_mr_collage(
    option_image_paths: list[str],
) -> BufferedInputFile | None:
    if not PILLOW_AVAILABLE or not UnidentifiedImageError:
        logger.error(
            "MR Collage: Pillow или его компоненты недоступны, коллаж не будет сгенерирован."
        )
        return None

    if len(option_image_paths) != 4:
        logger.error(
            f"MR Collage: Ожидалось 4 изображения, получено {len(option_image_paths)}"
        )
        return None

    images_to_collage = []
    for path in option_image_paths:
        try:
            img = Image.open(path)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img = img.resize(MR_COLLAGE_CELL_SIZE, Image.Resampling.LANCZOS)
            images_to_collage.append(img)
        except FileNotFoundError:
            logger.error(f"MR Collage: Файл изображения не найден: {path}")
            return None
        except UnidentifiedImageError:
            logger.error(
                f"MR Collage: Не удалось идентифицировать файл изображения: {path}"
            )
            return None
        except Exception as e:
            logger.error(
                f"MR Collage: Ошибка открытия/изменения размера изображения {path}: {e}"
            )
            return None

    collage_width = MR_COLLAGE_CELL_SIZE[0] * 2
    collage_height = MR_COLLAGE_CELL_SIZE[1] * 2
    collage = Image.new(
        "RGB", (collage_width, collage_height), MR_COLLAGE_BG_COLOR
    )

    try:
        collage.paste(images_to_collage[0], (0, 0))
        collage.paste(images_to_collage[1], (MR_COLLAGE_CELL_SIZE[0], 0))
        collage.paste(images_to_collage[2], (0, MR_COLLAGE_CELL_SIZE[1]))
        collage.paste(
            images_to_collage[3],
            (MR_COLLAGE_CELL_SIZE[0], MR_COLLAGE_CELL_SIZE[1]),
        )
    except Exception as e_paste:
        logger.error(f"MR Collage: Ошибка при сборке коллажа: {e_paste}")
        return None

    bio = BytesIO()
    bio.name = "mr_collage.png"
    try:
        collage.save(bio, "PNG")
        bio.seek(0)
        return BufferedInputFile(bio.read(), filename=bio.name)
    except Exception as e_save:
        logger.error(
            f"MR Collage: Ошибка сохранения изображения коллажа: {e_save}"
        )
        return None


def create_dummy_rt_image(image_path: str, number: int):
    if not PILLOW_AVAILABLE:
        logger.warning(
            f"RT Dummy: Pillow недоступен. Создание пустого файла-заглушки: {image_path}."
        )
        try:
            with open(image_path, "w") as f:
                f.write("")
        except IOError as e_io:
            logger.error(
                f"RT Dummy: Не удалось создать пустой файл-заглушку {image_path}: {e_io}"
            )
        return

    try:
        img = Image.new(
            "RGB",
            (100, 100),
            color=(
                random.randint(50, 200),
                random.randint(50, 200),
                random.randint(50, 200),
            ),
        )
        draw = ImageDraw.Draw(img)
        font = _get_font(
            STROOP_FONT_PATH, 30
        )  # Using a common font path from settings

        if font:
            # Attempt to center text, similar to Stroop functions
            try:
                bbox = draw.textbbox((0, 0), f"RT {number}", font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                x = (100 - tw) / 2
                y = (100 - th) / 2
                draw.text((x, y), f"RT {number}", fill=(0, 0, 0), font=font)
            except Exception as e_text_dummy:
                logger.warning(
                    f"RT Dummy: Ошибка измерения/отрисовки текста для {image_path}: "
                    f"{e_text_dummy}. Используется (10,10)."
                )
                draw.text(
                    (10, 10), f"RT {number}", fill=(0, 0, 0), font=font
                )  # Fallback position
        else:
            draw.text((10, 10), f"RT {number} (no font)", fill=(0, 0, 0))
            logger.error(
                f"RT Dummy: Не удалось загрузить шрифт для {image_path}."
            )

        img.save(image_path)
        logger.info(f"Создан RT файл-заглушка: {image_path}")
    except Exception as e:
        logger.error(f"Не удалось создать RT файл-заглушку {image_path}: {e}")
        try:
            with open(image_path, "w") as f:
                f.write("")
            logger.info(
                f"Создан пустой RT файл-заглушка {image_path} (ошибка Pillow)."
            )
        except IOError as e_io_fallback:
            logger.error(
                f"RT Dummy: Не удалось создать пустой файл-заглушку (fallback) {image_path}: {e_io_fallback}"
            )
