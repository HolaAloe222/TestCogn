# utils/bot_helpers.py
import logging
from typing import Optional, Dict, Any, Union

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


async def get_active_profile_from_fsm(
    state: FSMContext,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves the active user profile data from FSM storage.
    Prioritizes 'active_*' keys, then test-specific keys, then raw keys.
    Returns a dictionary with standardized keys: 'unique_id', 'name', 'age', 'telegram_id'.
    """
    data = await state.get_data()

    uid = data.get(
        "active_unique_id",
        data.get("unique_id_for_test", data.get("unique_id")),
    )
    name = data.get(
        "active_name", data.get("profile_name_for_test", data.get("name"))
    )
    age_raw = data.get(
        "active_age", data.get("profile_age_for_test", data.get("age"))
    )
    tgid = data.get(
        "active_telegram_id",
        data.get("profile_telegram_id_for_test", data.get("telegram_id")),
    )

    age: Optional[int] = None
    if age_raw is not None:
        try:
            age = int(age_raw)
        except ValueError:
            logger.warning(
                f"get_active_profile_from_fsm: Не удалось конвертировать возраст '{age_raw}' в int."
            )

    if uid is not None and name is not None and tgid is not None:
        try:
            profile = {
                "unique_id": str(uid),
                "name": str(name),
                "age": age,
                "telegram_id": int(tgid),
            }
            if profile["unique_id"].strip():
                return profile
            else:
                logger.warning(
                    "get_active_profile_from_fsm: UID пуст после конвертации в строку."
                )
                return None
        except ValueError as e:
            logger.error(
                f"get_active_profile_from_fsm: Ошибка конвертации данных профиля: {e}"
            )
            return None

    logger.debug(
        f"get_active_profile_from_fsm: Не удалось собрать полный профиль. Данные: uid='{uid}', name='{name}', age_raw='{age_raw}', tgid='{tgid}'"
    )
    return None


async def send_main_action_menu(
    bot_instance: Bot,
    trigger_event_or_message: Union[Message, CallbackQuery],
    keyboard_markup: InlineKeyboardMarkup,
    text: str = "Выберите действие:",
):
    chat_id: int
    target_message_id: Optional[int] = None

    source_message: Optional[Message] = None
    if isinstance(trigger_event_or_message, CallbackQuery):
        await trigger_event_or_message.answer()
        source_message = trigger_event_or_message.message
        if source_message:
            chat_id = source_message.chat.id
            target_message_id = source_message.message_id
            try:
                await bot_instance.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=target_message_id,
                    reply_markup=keyboard_markup,
                )
                return
            except TelegramBadRequest as e:
                logger.warning(
                    f"send_main_action_menu: Не удалось отредактировать (ID: {target_message_id}): {e}. Отправка нового."
                )
                try:
                    await bot_instance.delete_message(
                        chat_id, target_message_id
                    )
                except TelegramBadRequest:
                    logger.debug(
                        f"send_main_action_menu: Не удалось удалить старое (ID: {target_message_id})."
                    )
        else:
            chat_id = trigger_event_or_message.from_user.id
    else:
        source_message = trigger_event_or_message
        chat_id = source_message.chat.id

    try:
        await bot_instance.send_message(
            chat_id=chat_id, text=text, reply_markup=keyboard_markup
        )
    except Exception as e:
        logger.error(
            f"send_main_action_menu: Не удалось отправить новое сообщение меню в чат {chat_id}: {e}"
        )


# --- НОВЫЕ ПЕРЕМЕЩЕННЫЕ ФУНКЦИИ ---
async def _safe_delete_message(
    bot: Bot, chat_id: int, message_id: Optional[int], context_info: str = ""
):
    """Safely tries to delete a message, logging errors. (Moved from common_handlers)"""
    if message_id and chat_id:  # Ensure chat_id is also valid
        try:
            await bot.delete_message(chat_id, message_id)
            logger.debug(f"Сообщение ID {message_id} удалено. {context_info}")
        except TelegramBadRequest:
            logger.warning(
                f"Не удалось удалить сообщение ID {message_id} (уже удалено или недоступно). {context_info}"
            )
        except (
            Exception
        ) as e:  # Catch other potential errors like ChatNotFound, etc.
            logger.error(
                f"Ошибка удаления сообщения ID {message_id} в чате {chat_id}: {e}. {context_info}"
            )


async def _clear_fsm_and_set_profile(
    state: FSMContext, profile_data: Optional[Dict[str, Any]]
):
    """
    Clears all FSM data, sets the FSM state to None, and then sets only the provided profile data
    using standardized active_* keys. (Moved from common_handlers)
    """
    current_data = (
        await state.get_data()
    )  # Get data before clearing for logging or potential use
    logger.debug(
        f"_clear_fsm_and_set_profile: Текущие FSM ключи перед очисткой: {list(current_data.keys())}"
    )

    await state.clear()  # Clears state and all data

    if profile_data and profile_data.get("unique_id"):
        active_profile_data = {
            "active_unique_id": str(profile_data.get("unique_id")),
            "active_name": str(profile_data.get("name")),
            "active_age": (
                int(profile_data.get("age"))
                if profile_data.get("age") is not None
                else None
            ),
            "active_telegram_id": (
                int(profile_data.get("telegram_id"))
                if profile_data.get("telegram_id") is not None
                else None
            ),
        }
        active_profile_data_cleaned = {
            k: v for k, v in active_profile_data.items() if v is not None
        }

        if active_profile_data_cleaned.get("active_unique_id"):
            await state.set_data(active_profile_data_cleaned)
            logger.info(
                f"FSM очищен, установлен профиль: {active_profile_data_cleaned.get('active_unique_id')}"
            )
        else:
            logger.warning(
                "FSM очищен, но валидный профиль для установки не предоставлен после очистки ключей."
            )
    else:
        logger.info(
            "FSM очищен, профиль не предоставлен или невалиден для установки."
        )
    # Убедимся, что состояние действительно None после clear_state(clear_data=True)
    # await state.set_state(None) # This should be redundant if clear_data=True was effective
