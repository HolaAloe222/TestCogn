# main_bot.py
import asyncio
import logging
import os
import random  # Keep for now, verify usage in create_dummy_rt_image later

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# Pillow for image generation (optional check at startup)
try:
    from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    UnidentifiedImageError = None
    # Logging for missing Pillow will be done later

import config as bot_config
import settings as app_settings

from utils.excel_handler import initialize_excel_file
from utils.image_processors import create_dummy_rt_image
from handlers.tests.raven_matrices_handlers import (
    _parse_raven_filename,
)

from handlers import common_handlers
from handlers.tests import (
    corsi_handlers,
    stroop_handlers,
    reaction_time_handlers,
    verbal_fluency_handlers,
    mental_rotation_handlers,
    raven_matrices_handlers,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _ensure_directory(path: str, add_gitkeep: bool = False):
    """Ensures a directory exists and optionally adds a .gitkeep file."""
    try:
        os.makedirs(path, exist_ok=True)
        logger.info(f"Директория обеспечена: {path}")
        if add_gitkeep and not os.listdir(path):
            gitkeep_path = os.path.join(path, ".gitkeep")
            if not os.path.exists(gitkeep_path):
                with open(gitkeep_path, "w") as gk_f:
                    gk_f.write("")
                logger.info(f"Создан .gitkeep в {path}")
    except OSError as e:
        logger.error(f"Не удалось создать/обеспечить директорию {path}: {e}")
        # Consider if this error should be raised or handled more critically
        return False
    return True


def _populate_rt_resources():
    """Populates resources for the Reaction Time test."""
    if not _ensure_directory(app_settings.RT_IMAGES_DIR):
        logger.error(
            "RT Test: Не удалось создать директорию для изображений. "
            "Тест может не функционировать."
        )
        return

    for i in range(1, 11):  # Check for 10 images
        img_name = f"rt_img_{i}.png"
        img_path = os.path.join(app_settings.RT_IMAGES_DIR, img_name)
        if os.path.exists(img_path):
            app_settings.REACTION_TIME_IMAGE_POOL.append(img_path)
        elif Image and callable(create_dummy_rt_image):
            logger.info(f"RT Test: Создание dummy-изображения: {img_path}")
            try:
                create_dummy_rt_image(img_path, i)
                if os.path.exists(img_path):
                    app_settings.REACTION_TIME_IMAGE_POOL.append(img_path)
            except Exception as e_dummy:
                logger.error(
                    f"RT Test: Не удалось создать dummy-изображение {img_path}: {e_dummy}"
                )

    if app_settings.REACTION_TIME_IMAGE_POOL:
        logger.info(
            f"RT Test: Загружено/создано {len(app_settings.REACTION_TIME_IMAGE_POOL)} изображений."
        )
    else:
        logger.warning(
            "RT Test: Пул изображений пуст. Тест может не функционировать корректно."
        )


def _populate_mr_resources():
    """Populates resources for the Mental Rotation test."""
    # Ensure base directories exist and add .gitkeep if they are empty
    for mr_dir_path in [
        app_settings.MR_REFERENCES_DIR,
        app_settings.MR_CORRECT_PROJECTIONS_DIR,
        app_settings.MR_DISTRACTORS_DIR,
    ]:
        if not _ensure_directory(mr_dir_path, add_gitkeep=True):
            logger.error(
                f"MR Test: Критическая ошибка создания директории {mr_dir_path}. "
                "Ресурсы MR не будут загружены."
            )
            return  # Stop MR resource population if a dir fails

    # Populate MR_REFERENCE_FILES
    if os.path.isdir(app_settings.MR_REFERENCES_DIR):
        app_settings.MR_REFERENCE_FILES.clear()  # Ensure it's empty before populating
        for f_name in os.listdir(app_settings.MR_REFERENCES_DIR):
            full_path = os.path.join(app_settings.MR_REFERENCES_DIR, f_name)
            if os.path.isfile(full_path) and f_name.lower().endswith(
                (".png", ".jpg", ".jpeg")
            ):
                app_settings.MR_REFERENCE_FILES.append(f_name)
        logger.info(
            f"MR Test: Найдено {len(app_settings.MR_REFERENCE_FILES)} эталонных изображений."
        )

    # Populate MR_CORRECT_PROJECTIONS_MAP
    if (
        os.path.isdir(app_settings.MR_CORRECT_PROJECTIONS_DIR)
        and app_settings.MR_REFERENCE_FILES
    ):
        app_settings.MR_CORRECT_PROJECTIONS_MAP.clear()
        projection_files = [
            f
            for f in os.listdir(app_settings.MR_CORRECT_PROJECTIONS_DIR)
            if os.path.isfile(
                os.path.join(app_settings.MR_CORRECT_PROJECTIONS_DIR, f)
            )
            and f.lower().endswith((".png", ".jpg", ".jpeg"))
        ]
        for ref_file_name in app_settings.MR_REFERENCE_FILES:
            ref_base_name = os.path.splitext(ref_file_name)[0]
            app_settings.MR_CORRECT_PROJECTIONS_MAP[ref_file_name] = [
                proj_f_name
                for proj_f_name in projection_files
                if proj_f_name.startswith(ref_base_name + "_")
            ]
        if any(app_settings.MR_CORRECT_PROJECTIONS_MAP.values()):
            logger.info("MR Test: Карта корректных проекций заполнена.")
        else:
            logger.warning(
                "MR Test: Карта корректных проекций пуста или не найдено совпадений."
            )

    # Populate MR_ALL_DISTRACTORS_FILES
    if os.path.isdir(app_settings.MR_DISTRACTORS_DIR):
        app_settings.MR_ALL_DISTRACTORS_FILES.clear()
        app_settings.MR_ALL_DISTRACTORS_FILES.extend(
            [
                os.path.join(app_settings.MR_DISTRACTORS_DIR, f)
                for f in os.listdir(app_settings.MR_DISTRACTORS_DIR)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
                and os.path.isfile(
                    os.path.join(app_settings.MR_DISTRACTORS_DIR, f)
                )
            ]
        )
        if app_settings.MR_ALL_DISTRACTORS_FILES:
            logger.info(
                f"MR Test: Загружено {len(app_settings.MR_ALL_DISTRACTORS_FILES)} изображений-дистракторов."
            )
        else:
            logger.warning(
                f"MR Test: Не найдено изображений-дистракторов в {app_settings.MR_DISTRACTORS_DIR}."
            )


def _populate_raven_resources():
    """Populates resources for the Raven Matrices test."""
    if not _ensure_directory(app_settings.RAVEN_BASE_DIR, add_gitkeep=True):
        logger.error(
            "Raven Test: Не удалось создать директорию для изображений. "
            "Тест может не функционировать."
        )
        return

    app_settings.RAVEN_ALL_TASK_FILES.clear()
    for f_name in os.listdir(app_settings.RAVEN_BASE_DIR):
        if f_name.lower().endswith((".png", ".jpg", ".jpeg")):
            full_path = os.path.join(app_settings.RAVEN_BASE_DIR, f_name)
            if not os.path.isfile(full_path):
                continue
            try:
                _, correct_opt, num_opts = _parse_raven_filename(f_name)
                if correct_opt is not None and num_opts is not None:
                    app_settings.RAVEN_ALL_TASK_FILES.append(f_name)
            except (
                Exception
            ) as e_parse:  # Assuming _parse_raven_filename might raise error on invalid format
                logger.warning(
                    f"Raven Test: Не удалось распарсить имя файла {f_name}: {e_parse}"
                )

    if app_settings.RAVEN_ALL_TASK_FILES:
        logger.info(
            f"Raven Test: Загружено {len(app_settings.RAVEN_ALL_TASK_FILES)} валидных файлов задач."
        )
    else:
        logger.warning(
            f"Raven Test: Не найдено валидных файлов задач в {app_settings.RAVEN_BASE_DIR}."
        )


def initialize_application_resources():
    """Initializes Excel, loads image pools, creates directories."""
    logger.info("Инициализация ресурсов приложения...")

    if Image is None:
        logger.error(
            "Библиотека Pillow (PIL) не установлена. "
            "Генерация изображений для некоторых тестов будет невозможна или ограничена."
        )

    # 1. Initialize Excel file
    try:
        initialize_excel_file()
        logger.info("Excel файл инициализирован успешно.")
    except Exception as e_excel:
        logger.error(
            f"Критическая ошибка инициализации Excel файла: {e_excel}"
        )
        # Decide if application should exit or continue
        # return # Example: exit if Excel is critical

    # 2. Create base 'images' directory
    if not _ensure_directory("images"):
        # Depending on severity, you might want to exit
        logger.error(
            "Не удалось создать/обеспечить базовую директорию 'images'. "
            "Некоторые тесты могут не работать."
        )

    # 3. Populate resources for each test
    _populate_rt_resources()
    _populate_mr_resources()
    _populate_raven_resources()

    logger.info("Ресурсы приложения инициализированы.")


async def main():
    initialize_application_resources()

    bot = Bot(
        token=bot_config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Default storage is MemoryStorage, explicitly setting for clarity
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(common_handlers.router)
    dp.include_router(corsi_handlers.router)
    dp.include_router(stroop_handlers.router)
    dp.include_router(reaction_time_handlers.router)
    dp.include_router(verbal_fluency_handlers.router)
    dp.include_router(mental_rotation_handlers.router)
    dp.include_router(raven_matrices_handlers.router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск поллинга...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Ошибка поллинга: {e}", exc_info=True)
    finally:
        logger.info("Остановка бота и закрытие сессии...")
        await bot.session.close()
        logger.info("Сессия бота закрыта.")


if __name__ == "__main__":
    logger.info("Запуск бота...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную (KeyboardInterrupt).")
    except Exception as e_global:
        logger.critical(
            f"Глобальная ошибка во время выполнения бота: {e_global}",
            exc_info=True,
        )
    finally:
        logger.info("Завершение работы бота.")
