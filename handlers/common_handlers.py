# handlers/common_handlers.py
import asyncio
import logging
import os
from typing import Union, Optional  # Added Optional

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    FSInputFile,
)

from fsm_states import (
    UserData,
    CorsiTestStates,
    StroopTestStates,
    ReactionTimeTestStates,
    VerbalFluencyStates,
    MentalRotationStates,
    RavenMatricesStates,
)
from keyboards import (
    ACTION_SELECTION_KEYBOARD_NEW,
    ACTION_SELECTION_KEYBOARD_RETURNING,
    IKB,
)
from settings import EXCEL_FILENAME, BASE_HEADERS
from utils.bot_helpers import (
    get_active_profile_from_fsm,
    send_main_action_menu,
    _safe_delete_message,
    _clear_fsm_and_set_profile,
)
from utils.excel_handler import (
    check_if_corsi_results_exist,
    check_if_stroop_results_exist,
    check_if_reaction_time_results_exist,
    check_if_verbal_fluency_results_exist,
    check_if_mental_rotation_results_exist,
    check_if_raven_matrices_results_exist,
    create_user_profile_in_excel,
    find_user_profile_in_excel,
    get_all_user_data_from_excel,
)
from .tests import (
    corsi_handlers,
    stroop_handlers,
    reaction_time_handlers,
    verbal_fluency_handlers,
    mental_rotation_handlers,
    raven_matrices_handlers,
)

logger = logging.getLogger(__name__)
router = Router()

# --- Constants for new unauthorized user handling ---
NEW_UNAUTHORIZED_PROMPT_TEXT = "Для доступа к этой функции необходимо войти или зарегистрироваться. Пожалуйста, выберите один из вариантов:"
NEW_UNAUTHORIZED_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [IKB(text="Регистрация", callback_data="user_is_new")],
        [
            IKB(
                text="Вход по моему UID",
                callback_data="user_is_returning",
            )
        ],
    ]
)

# Keyboard for UID not found scenario
UID_FAIL_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [IKB(text="Ввести UID снова", callback_data="try_id_again")],
        [
            IKB(
                text="Новая регистрация",
                callback_data="register_new_after_fail",
            )
        ],
    ]
)

TEST_REGISTRY = {
    "initiate_corsi_test": {
        "name": "Тест Корси",
        "fsm_group_class": CorsiTestStates,
        "start_function": corsi_handlers.start_corsi_test,
        "save_function": corsi_handlers.save_corsi_results,
        "cleanup_function": corsi_handlers.cleanup_corsi_messages,
        "results_exist_check": check_if_corsi_results_exist,
        "requires_active_profile": True,
    },
    "initiate_stroop_test": {
        "name": "Тест Струпа",
        "fsm_group_class": StroopTestStates,
        "start_function": stroop_handlers.start_stroop_test,
        "save_function": stroop_handlers.save_stroop_results,
        "cleanup_function": stroop_handlers.cleanup_stroop_ui,
        "results_exist_check": check_if_stroop_results_exist,
        "requires_active_profile": True,
    },
    "initiate_reaction_time_test": {
        "name": "Тест на Скорость Реакции",
        "fsm_group_class": ReactionTimeTestStates,
        "start_function": reaction_time_handlers.start_reaction_time_test,
        "save_function": reaction_time_handlers.save_reaction_time_results,
        "cleanup_function": reaction_time_handlers.cleanup_reaction_time_ui,
        "results_exist_check": check_if_reaction_time_results_exist,
        "requires_active_profile": True,
        "end_test_function": reaction_time_handlers._rt_go_to_main_menu_or_clear,
    },
    "initiate_verbal_fluency_test": {
        "name": "Тест на вербальную беглость",
        "fsm_group_class": VerbalFluencyStates,
        "start_function": verbal_fluency_handlers.start_verbal_fluency_test,
        "save_function": verbal_fluency_handlers.save_verbal_fluency_results,
        "cleanup_function": verbal_fluency_handlers.cleanup_verbal_fluency_ui,
        "results_exist_check": check_if_verbal_fluency_results_exist,
        "requires_active_profile": True,
        "end_test_function": verbal_fluency_handlers._end_verbal_fluency_test,
    },
    "initiate_mental_rotation_test": {
        "name": "Тест умственного вращения",
        "fsm_group_class": MentalRotationStates,
        "start_function": mental_rotation_handlers.start_mental_rotation_test,
        "save_function": mental_rotation_handlers.save_mental_rotation_results,
        "cleanup_function": mental_rotation_handlers.cleanup_mental_rotation_ui,
        "results_exist_check": check_if_mental_rotation_results_exist,
        "requires_active_profile": True,
        "end_test_function": mental_rotation_handlers._finish_mental_rotation_test,
    },
    "initiate_raven_matrices_test": {
        "name": "Прогрессивные матрицы Равена",
        "fsm_group_class": RavenMatricesStates,
        "start_function": raven_matrices_handlers.start_raven_matrices_test,
        "save_function": raven_matrices_handlers.save_raven_matrices_results,
        "cleanup_function": raven_matrices_handlers.cleanup_raven_ui,
        "results_exist_check": check_if_raven_matrices_results_exist,
        "requires_active_profile": True,
        "end_test_function": raven_matrices_handlers._finish_raven_matrices_test,
    },
}


# --- Command Handlers ---
@router.message(CommandStart())
async def start_command_handler(
    message: Message, state: FSMContext, bot: Bot
):  # Added bot
    logger.info(
        f"Пользователь {message.from_user.id} инициировал команду /start."
    )
    await state.clear()
    await state.set_state(UserData.waiting_for_first_time_response)
    # Используем унифицированный текст и клавиатуру, как при неавторизованном доступе
    start_prompt_msg = await bot.send_message(  # Changed to bot.send_message for consistency
        chat_id=message.chat.id,
        text="Здравствуйте! Вы впервые пользуетесь этим ботом или хотите войти?",  # Слегка измененный текст для /start
        reply_markup=NEW_UNAUTHORIZED_KEYBOARD,  # Используем ту же клавиатуру
    )
    await state.update_data(
        start_prompt_message_id=start_prompt_msg.message_id
    )


@router.message(Command("menu"))
async def menu_command_handler(message: Message, state: FSMContext, bot: Bot):
    logger.info(
        f"Пользователь {message.from_user.id} инициировал команду /menu."
    )
    current_fsm_state_str = await state.get_state()

    is_in_test = False
    if current_fsm_state_str:
        for test_cfg in TEST_REGISTRY.values():
            fsm_group = test_cfg.get("fsm_group_class")
            if fsm_group and current_fsm_state_str.startswith(
                fsm_group.__name__
            ):
                is_in_test = True
                break

    if is_in_test:
        await message.answer(
            "Чтобы получить доступ к меню, пожалуйста, завершите или "
            "остановите текущий тест командой /stoptest или кнопкой в тесте."
        )
        return

    fsm_data = await state.get_data()
    status_msg_id = fsm_data.get("status_message_id_to_delete_later")
    if status_msg_id:
        await _safe_delete_message(
            bot, message.chat.id, status_msg_id, "/menu cleanup"
        )
        await state.update_data(status_message_id_to_delete_later=None)

    profile = await get_active_profile_from_fsm(state)
    if not profile:
        prompt_msg = (
            await message.answer(  # Используем message.answer для команд
                text=NEW_UNAUTHORIZED_PROMPT_TEXT,
                reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
            )
        )
        await state.update_data(
            unauthorized_prompt_message_id=prompt_msg.message_id
        )
        await state.set_state(UserData.waiting_for_first_time_response)
        return

    await send_main_action_menu(
        bot,
        message,
        ACTION_SELECTION_KEYBOARD_RETURNING,
        text="Главное меню. Выберите действие:",
    )


async def stop_test_command_handler(
    trigger_event: Union[Message, CallbackQuery],
    state: FSMContext,
    bot: Bot,
    called_from_test_button: bool = False,
):
    fsm_state_str = await state.get_state()
    active_test_cfg = None
    active_test_key = None
    test_name = "активного теста"  # Default for messages

    if fsm_state_str:
        for key, cfg in TEST_REGISTRY.items():
            fsm_group = cfg.get("fsm_group_class")
            if fsm_group and fsm_state_str.startswith(fsm_group.__name__):
                active_test_cfg = cfg
                active_test_key = key
                test_name = cfg["name"]
                break

    trigger_message_obj = (
        trigger_event
        if isinstance(trigger_event, Message)
        else trigger_event.message
    )
    chat_id = trigger_message_obj.chat.id

    fsm_data_before_stop = await state.get_data()
    ids_to_delete_this_time = []

    common_status_msg_id = fsm_data_before_stop.get(
        "status_message_id_to_delete_later"
    )
    if common_status_msg_id:
        ids_to_delete_this_time.append(common_status_msg_id)

    specific_end_routine_done_successfully = (
        False  # Flag to track if end_test_function completed
    )

    if active_test_cfg:
        logger.info(
            f"Остановка теста: {test_name} (ключ: {active_test_key}) пользователем."
        )

        if not called_from_test_button:
            try:
                stop_progress_msg = await trigger_message_obj.answer(
                    f"Пожалуйста, подождите, останавливаю тест: {test_name}..."
                )
                ids_to_delete_this_time.append(stop_progress_msg.message_id)
            except Exception as e:
                logger.error(
                    f"Не удалось отправить сообщение 'Останавливаю тест...': {e}"
                )

        end_func = active_test_cfg.get("end_test_function")
        save_func = active_test_cfg.get("save_function")
        cleanup_func = active_test_cfg.get("cleanup_function")

        if callable(end_func):
            logger.info(
                f"Stoptest: Вызов специфичной end_test_function для {test_name}"
            )
            try:
                # Pass 'is_stopped_by_command=True' or similar if the function expects it
                if active_test_key == "initiate_mental_rotation_test":
                    await mental_rotation_handlers._finish_mental_rotation_test(
                        state, bot, chat_id, True
                    )
                elif active_test_key == "initiate_raven_matrices_test":
                    await raven_matrices_handlers._finish_raven_matrices_test(
                        state, bot, chat_id, True
                    )
                elif active_test_key == "initiate_verbal_fluency_test":
                    await verbal_fluency_handlers._end_verbal_fluency_test(
                        state, bot, True, trigger_event=trigger_event
                    )
                elif active_test_key == "initiate_reaction_time_test":
                    await reaction_time_handlers._rt_go_to_main_menu_or_clear(
                        state, trigger_message_obj, bot
                    )
                # Add other tests here if their end_test_function has a specific signature for stopping
                else:  # Generic call if signature is unknown or simpler
                    await end_func(
                        state, bot, True
                    )  # Assuming a generic (state, bot, interrupted) signature

                specific_end_routine_done_successfully = True
                logger.info(
                    f"Stoptest: Специфичная end_test_function для {test_name} выполнена."
                )
            except Exception as e:
                logger.error(
                    f"Ошибка в end_test_function для {test_name}: {e}",
                    exc_info=True,
                )
                specific_end_routine_done_successfully = False

        if not specific_end_routine_done_successfully:
            logger.info(
                f"Stoptest: Запуск общего save/cleanup для {test_name} (end_func не было, не выполнилась или не помечена как успешная)."
            )
            if callable(save_func):
                try:
                    # Adjust based on actual save_func signatures
                    if active_test_key in [
                        "initiate_corsi_test",
                        "initiate_stroop_test",
                    ]:
                        await save_func(
                            trigger_message_obj,
                            state,
                            bot,
                            is_interrupted=True,
                        )
                    else:
                        await save_func(state, is_interrupted=True)
                except Exception as e_save:
                    logger.error(
                        f"Ошибка в общем save_func для {test_name}: {e_save}",
                        exc_info=True,
                    )

            if callable(cleanup_func):
                try:
                    await cleanup_func(
                        state, bot, final_text=f"Тест '{test_name}' прерван."
                    )
                except Exception as e_cleanup:
                    logger.error(
                        f"Ошибка в общем cleanup_func для {test_name}: {e_cleanup}",
                        exc_info=True,
                    )

        profile_after_test_ops = await get_active_profile_from_fsm(state)
        await _clear_fsm_and_set_profile(
            state, profile_after_test_ops
        )  # This sets state to None if profile is kept

        # Send main menu ONLY if a specific end routine (which might send its own menu) was NOT successfully done.
        if profile_after_test_ops:
            if not specific_end_routine_done_successfully:
                logger.info(
                    f"Stoptest: Отправка главного меню из stop_test_command_handler для {test_name}."
                )
                await asyncio.sleep(0.1)
                await send_main_action_menu(
                    bot,
                    trigger_message_obj,
                    ACTION_SELECTION_KEYBOARD_RETURNING,
                    text="Выберите действие:",
                )
            else:
                logger.info(
                    f"Stoptest: Главное меню НЕ отправляется из stop_test_command_handler для {test_name}, т.к. end_routine выполнилась."
                )
        else:  # Profile is None after stopping
            await trigger_message_obj.answer(
                f"Тест '{test_name}' остановлен. Профиль не найден. Пожалуйста, /start."
            )

    elif (
        not called_from_test_button
    ):  # No active test, and /stoptest was by command
        await trigger_message_obj.answer(
            "Нет активного теста для остановки. Пожалуйста, /start."
        )

    for msg_id in ids_to_delete_this_time:
        await _safe_delete_message(
            bot,
            chat_id,
            msg_id,
            "stop_test_command_handler final message cleanup",
        )


@router.message(Command("stoptest"))
async def stop_test_command_wrapper(
    message: Message, state: FSMContext, bot: Bot
):
    await stop_test_command_handler(
        message, state, bot, called_from_test_button=False
    )


@router.callback_query(F.data == "request_test_stop", StateFilter("*"))
async def handle_request_test_stop_from_button(
    callback: CallbackQuery, state: FSMContext, bot: Bot
):
    await callback.answer(
        "Запрос на остановку теста принят...", show_alert=False
    )
    await stop_test_command_handler(
        callback, state, bot, called_from_test_button=True
    )


@router.message(Command("restart"))
async def command_restart_bot_session_handler(
    message: Message, state: FSMContext, bot: Bot
):
    logger.info(
        f"Пользователь {message.from_user.id} инициировал команду /restart."
    )
    current_fsm_state_str = await state.get_state()

    if (
        current_fsm_state_str
        and current_fsm_state_str != UserData.waiting_for_first_time_response
    ):  # Avoid stopping if already at start prompt
        logger.info(
            f"/restart вызван во время активного состояния FSM: {current_fsm_state_str}. Попытка остановить тест."
        )
        # Treat as button press to avoid "stopping test..." message if not in test.
        await stop_test_command_handler(
            message, state, bot, called_from_test_button=True
        )

    await _clear_fsm_and_set_profile(state, None)
    await message.answer(
        "Все текущие операции были остановлены, ваш профиль и состояние теста в этой сессии сброшены.\n"
        "Пожалуйста, используйте команду /start для нового сеанса или входа."
    )


# --- User Registration and Login Flow ---
async def _handle_next_registration_step(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    next_text: str,
    next_state: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    message_to_edit_id: Optional[int] = None,
):
    """Helper to edit a message or send a new one for dialog steps."""
    sent_message = None
    if message_to_edit_id:
        try:
            await bot.edit_message_text(
                text=next_text,
                chat_id=chat_id,
                message_id=message_to_edit_id,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,  # Assuming HTML for consistency
            )
            sent_message = message_to_edit_id  # ID of the edited message
        except TelegramBadRequest as e:
            logger.warning(
                f"Failed to edit message {message_to_edit_id} to '{next_text[:30]}...': {e}. Sending new."
            )
            # If edit fails, the old message might still be there.
            # Decide if deletion is needed here or by the caller context.
            # For now, just send new.
            new_msg_obj = await bot.send_message(
                chat_id,
                next_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
            sent_message = new_msg_obj.message_id
    else:
        new_msg_obj = await bot.send_message(
            chat_id,
            next_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
        sent_message = new_msg_obj.message_id

    if next_state:
        await state.set_state(next_state)

    # Store the ID of the bot's current dialog message for the next step
    if sent_message:
        await state.update_data(current_dialog_message_id=sent_message)


@router.callback_query(
    F.data == "user_is_new", UserData.waiting_for_first_time_response
)
async def handle_user_is_new_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer()
    data = await state.get_data()
    # Prioritize unauthorized_prompt_message_id if it exists (coming from unauthorized access)
    # Otherwise, use start_prompt_message_id (coming from /start)
    original_prompt_message_id = data.get(
        "unauthorized_prompt_message_id"
    ) or data.get("start_prompt_message_id")

    chat_id = cb.message.chat.id if cb.message else cb.from_user.id
    next_text = "Рад знакомству! Пожалуйста, введите ваше имя:"

    await _handle_next_registration_step(
        bot,
        chat_id,
        state,
        next_text,
        UserData.waiting_for_name,
        message_to_edit_id=(
            original_prompt_message_id if cb.message else None
        ),  # Only edit if cb.message exists
    )
    # Clear the used prompt ID from FSM
    if data.get("unauthorized_prompt_message_id"):
        await state.update_data(unauthorized_prompt_message_id=None)
    elif data.get("start_prompt_message_id"):
        await state.update_data(start_prompt_message_id=None)


@router.callback_query(
    F.data == "user_is_returning", UserData.waiting_for_first_time_response
)
async def handle_user_is_returning_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer()
    data = await state.get_data()
    original_prompt_message_id = data.get(
        "unauthorized_prompt_message_id"
    ) or data.get("start_prompt_message_id")

    chat_id = cb.message.chat.id if cb.message else cb.from_user.id
    next_text = "Пожалуйста, введите ваш Уникальный Идентификатор (UID):"

    await _handle_next_registration_step(
        bot,
        chat_id,
        state,
        next_text,
        UserData.waiting_for_unique_id,
        message_to_edit_id=original_prompt_message_id if cb.message else None,
    )
    if data.get("unauthorized_prompt_message_id"):
        await state.update_data(unauthorized_prompt_message_id=None)
    elif data.get("start_prompt_message_id"):
        await state.update_data(start_prompt_message_id=None)


@router.message(UserData.waiting_for_name)
async def process_name_input(message: Message, state: FSMContext, bot: Bot):
    name = message.text.strip() if message.text else ""
    chat_id = message.chat.id
    await _safe_delete_message(
        bot, chat_id, message.message_id, "user name input"
    )

    if not name or len(name) < 2:
        # Re-send prompt or edit previous if possible (more complex for error states)
        # For now, send new error message.
        error_text = "Имя не может быть пустым и должно содержать хотя бы 2 символа. Попробуйте еще раз."
        # Here, we might want to edit the `current_dialog_message_id` if it was the "Enter name" prompt.
        data = await state.get_data()
        bot_prompt_id = data.get("current_dialog_message_id")
        await _handle_next_registration_step(
            bot,
            chat_id,
            state,
            error_text,
            UserData.waiting_for_name,
            message_to_edit_id=bot_prompt_id,
        )
        return

    await state.update_data(name_for_registration=name)

    data = await state.get_data()
    bot_prompt_id = data.get("current_dialog_message_id")
    age_prompt_text = f"Приятно, {name}! Введите ваш возраст (полных лет):"
    await _handle_next_registration_step(
        bot,
        chat_id,
        state,
        age_prompt_text,
        UserData.waiting_for_age,
        message_to_edit_id=bot_prompt_id,
    )


@router.message(UserData.waiting_for_age)
async def process_age_input(message: Message, state: FSMContext, bot: Bot):
    age_str = message.text.strip() if message.text else ""
    chat_id = message.chat.id
    await _safe_delete_message(
        bot, chat_id, message.message_id, "user age input"
    )

    try:
        age_val = int(age_str)
        if not (0 < age_val < 120):
            raise ValueError("Age out of range")
    except ValueError:
        error_text = (
            "Пожалуйста, введите корректный возраст (число от 1 до 119)."
        )
        data = await state.get_data()
        bot_prompt_id = data.get("current_dialog_message_id")
        await _handle_next_registration_step(
            bot,
            chat_id,
            state,
            error_text,
            UserData.waiting_for_age,
            message_to_edit_id=bot_prompt_id,
        )
        return

    fsm_data = await state.get_data()
    name = fsm_data.get("name_for_registration")
    bot_prompt_id = fsm_data.get("current_dialog_message_id")

    new_uid = await asyncio.to_thread(
        create_user_profile_in_excel, name, age_val, message.from_user.id
    )

    if new_uid:
        profile_data_to_set = {
            "unique_id": new_uid,
            "name": name,
            "age": age_val,
            "telegram_id": message.from_user.id,
        }
        await _clear_fsm_and_set_profile(
            state, profile_data_to_set
        )  # Sets state to None

        success_text = (
            f"Спасибо, {name}! Регистрация прошла успешно.\n"
            f"<b>Ваш Уникальный Идентификатор (UID): {new_uid}</b>\n"
            "Пожалуйста, сохраните его для будущего входа."
        )
        # Edit previous "Enter age" prompt to success message
        if bot_prompt_id:
            try:
                await bot.edit_message_text(
                    text=success_text,
                    chat_id=chat_id,
                    message_id=bot_prompt_id,
                    reply_markup=None,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramBadRequest:
                await bot.send_message(
                    chat_id, success_text, parse_mode=ParseMode.HTML
                )
        else:
            await bot.send_message(
                chat_id, success_text, parse_mode=ParseMode.HTML
            )

        await state.update_data(
            current_dialog_message_id=None
        )  # Clear dialog message ID
        await send_main_action_menu(
            bot, message, ACTION_SELECTION_KEYBOARD_NEW
        )
    else:
        error_text = "Произошла ошибка во время регистрации. Пожалуйста, попробуйте /start еще раз."
        if bot_prompt_id:
            try:
                await bot.edit_message_text(
                    text=error_text,
                    chat_id=chat_id,
                    message_id=bot_prompt_id,
                    reply_markup=None,
                )
            except TelegramBadRequest:
                await bot.send_message(chat_id, error_text)
        else:
            await bot.send_message(chat_id, error_text)

        await _clear_fsm_and_set_profile(state, None)


@router.message(UserData.waiting_for_unique_id)
async def process_unique_id_input(
    message: Message, state: FSMContext, bot: Bot
):
    uid_str_input = message.text.strip() if message.text else ""
    chat_id = message.chat.id
    await _safe_delete_message(
        bot, chat_id, message.message_id, "user UID input"
    )

    if not uid_str_input:
        error_text = "UID не может быть пустым. Пожалуйста, попробуйте снова."
        data = await state.get_data()
        bot_prompt_id = data.get("current_dialog_message_id")
        await _handle_next_registration_step(
            bot,
            chat_id,
            state,
            error_text,
            UserData.waiting_for_unique_id,
            message_to_edit_id=bot_prompt_id,
        )
        return

    found_profile_data = await asyncio.to_thread(
        find_user_profile_in_excel, uid_str_input, message.from_user.id
    )

    data = await state.get_data()
    bot_prompt_id = data.get("current_dialog_message_id")

    if found_profile_data:
        await _clear_fsm_and_set_profile(
            state, found_profile_data
        )  # Sets state to None
        current_profile_name = found_profile_data.get("name", "Пользователь")
        welcome_text = f"С возвращением, {current_profile_name}!"

        if bot_prompt_id:
            try:
                await bot.edit_message_text(
                    text=welcome_text,
                    chat_id=chat_id,
                    message_id=bot_prompt_id,
                    reply_markup=None,
                )
            except TelegramBadRequest:
                await bot.send_message(chat_id, welcome_text)
        else:
            await bot.send_message(chat_id, welcome_text)
        await state.update_data(current_dialog_message_id=None)
        await send_main_action_menu(
            bot, message, ACTION_SELECTION_KEYBOARD_RETURNING
        )
    else:
        fail_text = "UID не найден. Проверьте ввод или зарегистрируйтесь."
        # Edit the "Enter UID" prompt to "UID not found" with new keyboard
        sent_fail_msg_id = None
        if bot_prompt_id:
            try:
                await bot.edit_message_text(
                    text=fail_text,
                    chat_id=chat_id,
                    message_id=bot_prompt_id,
                    reply_markup=UID_FAIL_KEYBOARD,
                )
                sent_fail_msg_id = bot_prompt_id
            except TelegramBadRequest:
                new_msg = await bot.send_message(
                    chat_id, fail_text, reply_markup=UID_FAIL_KEYBOARD
                )
                sent_fail_msg_id = new_msg.message_id
        else:
            new_msg = await bot.send_message(
                chat_id, fail_text, reply_markup=UID_FAIL_KEYBOARD
            )
            sent_fail_msg_id = new_msg.message_id

        await state.update_data(
            uid_not_found_prompt_message_id=sent_fail_msg_id,
            current_dialog_message_id=None,
        )
        # State UserData.waiting_for_unique_id remains for try_id_again or register_new_after_fail


@router.callback_query(
    F.data == "try_id_again", UserData.waiting_for_unique_id
)
async def handle_try_id_again_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer()
    data = await state.get_data()
    # The message with "UID not found" buttons is cb.message
    # This ID was stored as uid_not_found_prompt_message_id
    message_to_edit_id = data.get("uid_not_found_prompt_message_id")

    chat_id = cb.message.chat.id if cb.message else cb.from_user.id
    next_text = "Введите ваш UID еще раз:"

    await _handle_next_registration_step(
        bot,
        chat_id,
        state,
        next_text,
        UserData.waiting_for_unique_id,  # State remains same
        message_to_edit_id=(
            message_to_edit_id
            if cb.message and cb.message.message_id == message_to_edit_id
            else None
        ),
    )
    await state.update_data(
        uid_not_found_prompt_message_id=None
    )  # Clear specific prompt ID


@router.callback_query(
    F.data == "register_new_after_fail", UserData.waiting_for_unique_id
)
async def handle_register_new_after_fail_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer()
    data = await state.get_data()
    message_to_edit_id = data.get("uid_not_found_prompt_message_id")

    chat_id = cb.message.chat.id if cb.message else cb.from_user.id
    next_text = "Хорошо, давайте зарегистрируемся. Как вас зовут?"

    await _handle_next_registration_step(
        bot,
        chat_id,
        state,
        next_text,
        UserData.waiting_for_name,
        message_to_edit_id=(
            message_to_edit_id
            if cb.message and cb.message.message_id == message_to_edit_id
            else None
        ),
    )
    await state.update_data(uid_not_found_prompt_message_id=None)


# --- Test Selection and Start Flow ---
@router.callback_query(F.data == "select_specific_test", StateFilter(None))
async def on_select_specific_test_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    profile = await get_active_profile_from_fsm(state)
    if not profile:
        await cb.answer()  # Answer callback first
        # cb.message is the message with "select_specific_test" button
        # We want to edit this message to show the unauthorized prompt
        if cb.message:
            try:
                await bot.edit_message_text(
                    chat_id=cb.message.chat.id,
                    message_id=cb.message.message_id,
                    text=NEW_UNAUTHORIZED_PROMPT_TEXT,
                    reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
                )
                await state.update_data(
                    unauthorized_prompt_message_id=cb.message.message_id
                )
            except TelegramBadRequest:
                # Fallback: delete old button message and send new prompt
                await _safe_delete_message(
                    bot,
                    cb.message.chat.id,
                    cb.message.message_id,
                    "del old select_specific_test btn",
                )
                new_prompt = await bot.send_message(
                    cb.message.chat.id,
                    NEW_UNAUTHORIZED_PROMPT_TEXT,
                    reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
                )
                await state.update_data(
                    unauthorized_prompt_message_id=new_prompt.message_id
                )
        else:  # Should not happen with callbackquery with message
            new_prompt = await bot.send_message(
                cb.from_user.id,
                NEW_UNAUTHORIZED_PROMPT_TEXT,
                reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
            )
            await state.update_data(
                unauthorized_prompt_message_id=new_prompt.message_id
            )

        await state.set_state(UserData.waiting_for_first_time_response)
        return

    # Существующая логика для авторизованного пользователя (остается)
    btns = [
        [IKB(text=cfg["name"], callback_data=f"select_test_{key}")]
        for key, cfg in TEST_REGISTRY.items()
    ]
    if not btns:
        await cb.answer("Нет доступных тестов.", show_alert=True)
        return

    await cb.answer()
    if cb.message:  # Edit current message (menu) to show test list
        try:
            await bot.edit_message_text(
                "Выберите тест:",
                chat_id=cb.message.chat.id,
                message_id=cb.message.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            )
        except TelegramBadRequest:  # Fallback
            await _safe_delete_message(
                bot,
                cb.message.chat.id,
                cb.message.message_id,
                "select_specific_test menu edit failed",
            )
            await bot.send_message(
                cb.message.chat.id,
                "Выберите тест:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            )


@router.callback_query(F.data.startswith("select_test_"), StateFilter(None))
async def on_test_selected_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    test_key_selected = cb.data.replace("select_test_", "")
    if test_key_selected not in TEST_REGISTRY:
        await cb.answer("Выбранный тест не найден.", show_alert=True)
        return

    cfg = TEST_REGISTRY[test_key_selected]
    profile = await get_active_profile_from_fsm(state)

    if not profile:
        await cb.answer()  # Answer callback first
        if cb.message:  # cb.message is the test selection list
            try:
                await bot.edit_message_text(
                    chat_id=cb.message.chat.id,
                    message_id=cb.message.message_id,
                    text=NEW_UNAUTHORIZED_PROMPT_TEXT,
                    reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
                )
                await state.update_data(
                    unauthorized_prompt_message_id=cb.message.message_id
                )
            except TelegramBadRequest:
                await _safe_delete_message(
                    bot,
                    cb.message.chat.id,
                    cb.message.message_id,
                    "del old test list",
                )
                new_prompt = await bot.send_message(
                    cb.message.chat.id,
                    NEW_UNAUTHORIZED_PROMPT_TEXT,
                    reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
                )
                await state.update_data(
                    unauthorized_prompt_message_id=new_prompt.message_id
                )
        else:
            new_prompt = await bot.send_message(
                cb.from_user.id,
                NEW_UNAUTHORIZED_PROMPT_TEXT,
                reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
            )
            await state.update_data(
                unauthorized_prompt_message_id=new_prompt.message_id
            )

        await state.set_state(UserData.waiting_for_first_time_response)
        return

    # Существующая логика для авторизованного пользователя (остается)
    await cb.answer()
    if cb.message:  # Delete the test selection list message
        await _safe_delete_message(
            bot,
            cb.message.chat.id,
            cb.message.message_id,
            "Test selection list",
        )

    await state.update_data(
        pending_test_key_for_overwrite=test_key_selected,
        uid_for_result_check=profile.get("unique_id"),
    )
    # ... (rest of the function remains the same)
    results_already_exist = False
    if callable(cfg.get("results_exist_check")):
        uid_for_check = profile.get("unique_id")
        if uid_for_check:
            results_already_exist = await cfg["results_exist_check"](
                uid_for_check
            )
        else:
            logger.warning(
                f"Критически: Нет UID в профиле для results_exist_check для теста {test_key_selected}"
            )

    target_chat_id_for_test_flow = (
        cb.message.chat.id if cb.message else cb.from_user.id
    )

    if results_already_exist:
        kbd_overwrite = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    IKB(
                        text="Да, перезаписать",
                        callback_data="confirm_overwrite_test_results",
                    )
                ],
                [
                    IKB(
                        text="Нет, отмена",
                        callback_data="cancel_overwrite_test_results",
                    )
                ],
            ]
        )
        txt = f"У вас уже есть результаты для теста '{cfg['name']}'. Перезаписать?"
        overwrite_prompt_msg = await bot.send_message(
            chat_id=target_chat_id_for_test_flow,
            text=txt,
            reply_markup=kbd_overwrite,
        )
        await state.update_data(
            overwrite_confirmation_message_id=overwrite_prompt_msg.message_id
        )
        await state.set_state(UserData.waiting_for_test_overwrite_confirmation)
    else:
        prep_message_text = f"Подготовка к тесту: {cfg['name']}..."
        prep_message = await bot.send_message(
            chat_id=target_chat_id_for_test_flow, text=prep_message_text
        )
        await state.update_data(
            status_message_id_to_delete_later=prep_message.message_id
        )
        start_func = cfg.get("start_function")
        if callable(start_func):
            await start_func(cb, state, profile, bot)
        else:
            logger.error(
                f"Не найдена start_function для теста {test_key_selected}"
            )
            await bot.send_message(
                target_chat_id_for_test_flow, "Ошибка запуска теста."
            )
            await _safe_delete_message(
                bot,
                target_chat_id_for_test_flow,
                prep_message.message_id,
                "Test start function error",
            )
            await state.update_data(status_message_id_to_delete_later=None)


@router.callback_query(
    F.data == "confirm_overwrite_test_results",
    UserData.waiting_for_test_overwrite_confirmation,
)
async def handle_confirm_overwrite_test_results(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer()
    data = await state.get_data()
    test_key = data.get("pending_test_key_for_overwrite")
    overwrite_msg_id = data.get(
        "overwrite_confirmation_message_id"
    )  # This is cb.message.message_id
    target_chat_id = cb.message.chat.id if cb.message else cb.from_user.id

    # Edit the "overwrite?" prompt to "Starting test..." or delete and send new
    text_after_confirm = (
        f"Запускаем тест: {TEST_REGISTRY[test_key]['name']} (перезапись)..."
        if test_key and test_key in TEST_REGISTRY
        else "Запускаем тест (перезапись)..."
    )
    if cb.message and cb.message.message_id == overwrite_msg_id:
        try:
            await bot.edit_message_text(
                text=text_after_confirm,
                chat_id=target_chat_id,
                message_id=overwrite_msg_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            await _safe_delete_message(
                bot,
                target_chat_id,
                overwrite_msg_id,
                "del confirm_overwrite prompt",
            )
            await bot.send_message(
                target_chat_id, text_after_confirm
            )  # Send new status
    else:  # Fallback or if message IDs don't match
        if (
            overwrite_msg_id
        ):  # Still try to delete the original prompt if ID is known
            await _safe_delete_message(
                bot,
                target_chat_id,
                overwrite_msg_id,
                "del confirm_overwrite prompt",
            )
        await bot.send_message(target_chat_id, text_after_confirm)

    await state.update_data(
        overwrite_confirmation_message_id=None,  # Clear the specific prompt ID
        pending_test_key_for_overwrite=None,
        # status_message_id_to_delete_later will be set by the new status_msg or start_func
    )
    # ... (rest of the function logic for starting the test)
    if (
        not test_key or test_key not in TEST_REGISTRY
    ):  # Safety check from original
        await bot.send_message(
            target_chat_id,
            "Ошибка: тест не определен. Пожалуйста, выберите тест снова.",
        )
        profile_at_error = await get_active_profile_from_fsm(state)
        await _clear_fsm_and_set_profile(state, profile_at_error)
        if profile_at_error:
            await send_main_action_menu(
                bot, cb.message or cb, ACTION_SELECTION_KEYBOARD_RETURNING
            )
        return

    cfg = TEST_REGISTRY[test_key]
    profile = await get_active_profile_from_fsm(state)
    if not profile:
        # This case should ideally be handled before this point, but as a safeguard:
        unauth_prompt = await bot.send_message(
            target_chat_id,
            NEW_UNAUTHORIZED_PROMPT_TEXT,
            reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
        )
        await state.update_data(
            unauthorized_prompt_message_id=unauth_prompt.message_id,
            current_dialog_message_id=None,
        )
        await state.set_state(UserData.waiting_for_first_time_response)
        return

    # The message "Запускаем тест..." was already sent/edited. We might want its ID for status_message_id_to_delete_later
    # For simplicity, assume start_func or subsequent test messages will handle their own temporary messages.
    # Or, if text_after_confirm was sent as a new message, its ID could be stored.
    # Let's assume the status message is transient and will be replaced by test UI.

    await state.set_state(None)  # Reset from UserData state
    start_func = cfg.get("start_function")
    if callable(start_func):
        await start_func(
            cb, state, profile, bot
        )  # cb might be used for user/chat context
    else:
        logger.error(
            f"Не найдена start_function для теста {test_key} при перезаписи"
        )
        await bot.send_message(target_chat_id, "Ошибка запуска теста.")
        # status_message_id_to_delete_later was not explicitly set here after edit,
        # so no specific deletion needed for it here if start_func fails.


@router.callback_query(
    F.data == "cancel_overwrite_test_results",
    UserData.waiting_for_test_overwrite_confirmation,
)
async def handle_cancel_overwrite_test_results(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer("Запуск теста отменен.", show_alert=False)
    data = await state.get_data()
    test_key = data.get("pending_test_key_for_overwrite")
    overwrite_msg_id = data.get(
        "overwrite_confirmation_message_id"
    )  # This is cb.message.message_id
    target_chat_id = cb.message.chat.id if cb.message else cb.from_user.id

    test_name_display = (
        TEST_REGISTRY[test_key]["name"]
        if test_key and test_key in TEST_REGISTRY
        else "теста"
    )
    cancel_text = f"Запуск теста '{test_name_display}' отменен."

    if cb.message and cb.message.message_id == overwrite_msg_id:
        try:
            await bot.edit_message_text(
                text=cancel_text,
                chat_id=target_chat_id,
                message_id=overwrite_msg_id,
                reply_markup=None,
            )
        except TelegramBadRequest:
            await _safe_delete_message(
                bot,
                target_chat_id,
                overwrite_msg_id,
                "cancel_overwrite edit failed",
            )
            await bot.send_message(
                target_chat_id, cancel_text
            )  # Send new if edit fails
    else:  # Fallback if message ID mismatch or cb.message is None
        if (
            overwrite_msg_id
        ):  # Still try to delete the original prompt by its known ID
            await _safe_delete_message(
                bot,
                target_chat_id,
                overwrite_msg_id,
                "cancel_overwrite_del_by_id",
            )
        await bot.send_message(target_chat_id, cancel_text)

    profile_to_keep = await get_active_profile_from_fsm(state)
    await _clear_fsm_and_set_profile(
        state, profile_to_keep
    )  # Clears UserData state, sets profile
    await state.update_data(
        overwrite_confirmation_message_id=None,
        pending_test_key_for_overwrite=None,
        current_dialog_message_id=None,
    )

    await send_main_action_menu(
        bot,
        cb.message
        or cb,  # cb.message is the (now edited/deleted) prompt. Pass cb for user context.
        (
            ACTION_SELECTION_KEYBOARD_RETURNING
            if profile_to_keep
            else ACTION_SELECTION_KEYBOARD_NEW
        ),
    )


# --- Utility Commands ---
@router.message(Command("mydata"))
async def show_my_data_command(
    message: Message, state: FSMContext, bot: Bot  # bot added back
):
    profile = await get_active_profile_from_fsm(state)
    if not profile:
        prompt_msg = await message.answer(  # Using message.answer for commands
            text=NEW_UNAUTHORIZED_PROMPT_TEXT,
            reply_markup=NEW_UNAUTHORIZED_KEYBOARD,
        )
        await state.update_data(
            unauthorized_prompt_message_id=prompt_msg.message_id
        )
        await state.set_state(UserData.waiting_for_first_time_response)
        return

    # Существующая логика для авторизованного пользователя (остается)
    uid_to_check = str(profile.get("unique_id"))
    name_display = profile.get("name", "N/A")
    age_display = profile.get("age", "N/A")
    lines = [
        f"Данные для UID: <b>{uid_to_check}</b> (Имя: {name_display}, Возраст: {age_display})"
    ]
    excel_data = await asyncio.to_thread(
        get_all_user_data_from_excel, uid_to_check
    )
    if "error" in excel_data:
        lines.append(excel_data["error"])
    elif "info" in excel_data:
        lines.append(excel_data["info"])
    else:
        lines.append("--- Результаты тестов из файла ---")
        for header_name, display_val in excel_data.items():
            if header_name in BASE_HEADERS and header_name not in [
                "Telegram ID",
                "Unique ID",
            ]:
                if (
                    header_name == "Name"
                    and name_display != "N/A"
                    and name_display == display_val
                ):
                    continue
                if (
                    header_name == "Age"
                    and age_display is not None
                    and str(age_display) == display_val
                ):
                    continue
            lines.append(f"<b>{str(header_name)}:</b> {str(display_val)}")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(Command("export"))
async def export_data_to_excel_command(
    message: Message,
):  # No state or bot needed
    if os.path.exists(EXCEL_FILENAME):
        try:
            await message.reply_document(
                FSInputFile(EXCEL_FILENAME),
                caption="База данных пользователей и результатов.",
            )
        except Exception as e:
            logger.error(
                f"Не удалось отправить Excel файл: {e}", exc_info=True
            )
            await message.answer(f"Не удалось отправить файл: {e}")
    else:
        await message.answer(
            f"Файл данных '{EXCEL_FILENAME}' не найден на сервере."
        )


@router.callback_query(F.data == "logout_profile", StateFilter(None))
async def logout_profile_callback(
    cb: CallbackQuery, state: FSMContext, bot: Bot
):
    await cb.answer(
        "Ваш профиль был сброшен из текущей сессии.", show_alert=True
    )
    if cb.message:  # cb.message is the main menu
        # Edit the main menu to show logout confirmation, then send /start prompt
        try:
            await bot.edit_message_text(
                chat_id=cb.message.chat.id,
                message_id=cb.message.message_id,
                text="Профиль сброшен. Используйте /start для нового входа или регистрации.",
                reply_markup=None,  # Remove menu buttons
            )
        except TelegramBadRequest:  # Fallback if edit fails
            await _safe_delete_message(
                bot,
                cb.message.chat.id,
                cb.message.message_id,
                "logout_profile menu cleanup",
            )
            await bot.send_message(
                cb.from_user.id,
                "Профиль сброшен. Используйте /start для нового входа или регистрации.",
            )
    else:  # Should not happen, but safeguard
        await bot.send_message(
            cb.from_user.id,
            "Профиль сброшен. Используйте /start для нового входа или регистрации.",
        )

    await _clear_fsm_and_set_profile(state, None)
    # No need to send another message here as we edited or sent one above.


@router.callback_query(F.data == "run_test_battery", StateFilter(None))
async def on_run_test_battery_callback(
    cb: CallbackQuery,
):  # No state or bot needed for simple answer
    await cb.answer(
        "Функция 'Пройти батарею тестов' находится в разработке.",
        show_alert=True,
    )
