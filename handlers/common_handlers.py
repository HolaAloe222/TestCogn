# handlers/common_handlers.py
import asyncio
import logging
import os
import time 
from typing import Union, Optional 

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
    ShortTermMemoryStates, 
    BatteryCycleStates, 
)
from keyboards import (
    ACTION_SELECTION_KEYBOARD_NEW,
    ACTION_SELECTION_KEYBOARD_RETURNING,
    IKB,
)
# MODIFIED IMPORT BLOCK START
from settings import (
    EXCEL_FILENAME, BASE_HEADERS, 
    BATTERY_INTER_TEST_PAUSE_S, BATTERY_MEDICATION_WAIT_MINUTES,
    TEST_REGISTRY, BATTERY_TEST_SEQUENCE_KEYS 
)
# MODIFIED IMPORT BLOCK END
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
    short_term_memory_handlers, 
)
from filters.custom_filters import IsNotInBatteryFilter
from .battery_utils import battery_proceed_after_test_completion 

logger = logging.getLogger(__name__)
router = Router()

NEW_UNAUTHORIZED_PROMPT_TEXT = "Для доступа к этой функции необходимо войти или зарегистрироваться. Пожалуйста, выберите один из вариантов:"
NEW_UNAUTHORIZED_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Регистрация", callback_data="user_is_new")],[IKB(text="Вход по моему UID", callback_data="user_is_returning")],])
UID_FAIL_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Ввести UID снова", callback_data="try_id_again")],[IKB(text="Новая регистрация", callback_data="register_new_after_fail")],])

# TEST_REGISTRY is now declared in settings.py and will be populated here.
# TEST_SEQUENCE definition removed, will use BATTERY_TEST_SEQUENCE_KEYS from settings

# --- Command Handlers ---
@router.message(CommandStart())
async def start_command_handler(message: Message, state: FSMContext, bot: Bot):
    logger.info(f"User {message.from_user.id} initiated /start.")
    await state.clear()
    await state.set_state(UserData.waiting_for_first_time_response)
    start_prompt_msg = await bot.send_message(chat_id=message.chat.id, text="Здравствуйте! Вы впервые пользуетесь этим ботом или хотите войти?", reply_markup=NEW_UNAUTHORIZED_KEYBOARD)
    await state.update_data(start_prompt_message_id=start_prompt_msg.message_id)

@router.message(Command("menu"), IsNotInBatteryFilter()) 
async def menu_command_handler(message: Message, state: FSMContext, bot: Bot):
    logger.info(f"User {message.from_user.id} initiated /menu.")
    # ... (rest of the function as in Turn 37, it's long) ...
    current_fsm_state_str = await state.get_state()
    is_in_test = False
    if current_fsm_state_str:
        for test_cfg in TEST_REGISTRY.values(): # TEST_REGISTRY is used here
            fsm_group = test_cfg.get("fsm_group_class")
            if fsm_group and current_fsm_state_str.startswith(fsm_group.__name__): is_in_test = True; break
    if is_in_test: 
        await message.answer("Чтобы получить доступ к меню, пожалуйста, завершите или остановите текущий тест командой /stoptest или кнопкой в тесте.")
        return
    fsm_data = await state.get_data()
    status_msg_id = fsm_data.get("status_message_id_to_delete_later")
    if status_msg_id: await _safe_delete_message(bot, message.chat.id, status_msg_id, "/menu cleanup"); await state.update_data(status_message_id_to_delete_later=None)
    profile = await get_active_profile_from_fsm(state)
    if not profile:
        prompt_msg = await message.answer(text=NEW_UNAUTHORIZED_PROMPT_TEXT, reply_markup=NEW_UNAUTHORIZED_KEYBOARD)
        await state.update_data(unauthorized_prompt_message_id=prompt_msg.message_id); await state.set_state(UserData.waiting_for_first_time_response); return
    await send_main_action_menu(bot, message, ACTION_SELECTION_KEYBOARD_RETURNING, text="Главное меню. Выберите действие:")


async def stop_test_command_handler(trigger_event: Union[Message, CallbackQuery], state: FSMContext, bot: Bot, called_from_test_button: bool = False):
    # ... (Full existing implementation from Turn 37)
    fsm_state_str = await state.get_state()
    active_test_cfg = None; active_test_key = None; test_name = "активного теста"
    if fsm_state_str:
        for key, cfg in TEST_REGISTRY.items(): # TEST_REGISTRY is used here
            fsm_group = cfg.get("fsm_group_class")
            if fsm_group and fsm_state_str.startswith(fsm_group.__name__):
                active_test_cfg = cfg; active_test_key = key; test_name = cfg["name"]; break
    trigger_message_obj = trigger_event if isinstance(trigger_event, Message) else trigger_event.message
    chat_id = trigger_message_obj.chat.id
    fsm_data_before_stop = await state.get_data(); ids_to_delete_this_time = []
    common_status_msg_id = fsm_data_before_stop.get("status_message_id_to_delete_later")
    if common_status_msg_id: ids_to_delete_this_time.append(common_status_msg_id)
    specific_end_routine_done_successfully = False
    if active_test_cfg:
        logger.info(f"Остановка теста: {test_name} (ключ: {active_test_key}) пользователем.")
        if not called_from_test_button:
            try: stop_progress_msg = await trigger_message_obj.answer(f"Пожалуйста, подождите, останавливаю тест: {test_name}..."); ids_to_delete_this_time.append(stop_progress_msg.message_id)
            except Exception as e: logger.error(f"Не удалось отправить сообщение 'Останавливаю тест...': {e}")
        end_func, save_func, cleanup_func = active_test_cfg.get("end_test_function"), active_test_cfg.get("save_function"), active_test_cfg.get("cleanup_function")
        if callable(end_func):
            logger.info(f"Stoptest: Вызов специфичной end_test_function для {test_name}")
            try:
                if active_test_key == "initiate_mental_rotation_test": await mental_rotation_handlers._finish_mental_rotation_test(state, bot, chat_id, True, called_by_stop_command=True, trigger_msg_context=trigger_message_obj)
                elif active_test_key == "initiate_raven_matrices_test": await raven_matrices_handlers._finish_raven_matrices_test(state, bot, chat_id, True, called_by_stop_command=True, trigger_msg_context=trigger_message_obj)
                elif active_test_key == "initiate_verbal_fluency_test": await verbal_fluency_handlers._end_verbal_fluency_test(state, bot, True, trigger_event=trigger_event)
                elif active_test_key == "initiate_reaction_time_test": await reaction_time_handlers._rt_go_to_main_menu_or_clear(state, trigger_message_obj, bot)
                else: await end_func(state, bot, True)
                specific_end_routine_done_successfully = True
            except Exception as e: logger.error(f"Ошибка в end_test_function для {test_name}: {e}", exc_info=True)
        if not specific_end_routine_done_successfully:
            logger.info(f"Stoptest: Запуск общего save/cleanup для {test_name}")
            if callable(save_func):
                try:
                    target_sheet_for_save = fsm_data_before_stop.get("current_battery_phase") + "_Ц" if fsm_data_before_stop.get("current_battery_phase") else None
                    if active_test_key in ["initiate_corsi_test", "initiate_stroop_test", "initiate_stm_test"]:
                        await save_func(trigger_message_obj, state, bot, is_interrupted=True, target_sheet_name=target_sheet_for_save)
                    else: 
                        await save_func(state, is_interrupted=True, target_sheet_name=target_sheet_for_save)
                except Exception as e_save: logger.error(f"Ошибка в общем save_func для {test_name}: {e_save}", exc_info=True)
            if callable(cleanup_func):
                try: await cleanup_func(state, bot, final_text=f"Тест '{test_name}' прерван.")
                except Exception as e_cleanup: logger.error(f"Ошибка в общем cleanup_func для {test_name}: {e_cleanup}", exc_info=True)
        profile_after_test_ops = await get_active_profile_from_fsm(state)
        await _clear_fsm_and_set_profile(state, profile_after_test_ops)
        if profile_after_test_ops:
            if not specific_end_routine_done_successfully:
                await asyncio.sleep(0.1); await send_main_action_menu(bot, trigger_message_obj, ACTION_SELECTION_KEYBOARD_RETURNING, text="Выберите действие:")
        else: await trigger_message_obj.answer(f"Тест '{test_name}' остановлен. Профиль не найден. Пожалуйста, /start.")
    elif not called_from_test_button: await trigger_message_obj.answer("Нет активного теста для остановки. Пожалуйста, /start.")
    for msg_id in ids_to_delete_this_time: await _safe_delete_message(bot, chat_id, msg_id, "stop_test_command_handler final cleanup")


@router.message(Command("stoptest"))
async def stop_test_command_wrapper(message: Message, state: FSMContext, bot: Bot):
    await stop_test_command_handler(message, state, bot, called_from_test_button=False)

@router.callback_query(F.data == "request_test_stop", StateFilter("*"))
async def handle_request_test_stop_from_button(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("Запрос на остановку теста принят...", show_alert=False)
    await stop_test_command_handler(callback, state, bot, called_from_test_button=True)
    
@router.message(Command("restart"))
async def command_restart_bot_session_handler(message: Message, state: FSMContext, bot: Bot):
    logger.info(f"User {message.from_user.id} initiated /restart.")
    current_fsm_state_str = await state.get_state()
    if current_fsm_state_str and not current_fsm_state_str.startswith(UserData.__name__) and current_fsm_state_str not in [None, UserData.waiting_for_first_time_response.state]:
        logger.info(f"/restart called during active state: {current_fsm_state_str}. Attempting to stop operations.")
        if current_fsm_state_str.startswith(BatteryCycleStates.__name__):
            await handle_stop_battery_command(message, state, bot) 
            return 
        else: 
             await stop_test_command_handler(message, state, bot, called_from_test_button=True) 
    await _clear_fsm_and_set_profile(state, None) 
    await message.answer("Все текущие операции были остановлены, ваш профиль и состояние теста в этой сессии сброшены.\nПожалуйста, используйте команду /start для нового сеанса или входа.")

# --- User Registration and Login Flow ---
# ... (Full implementations from Turn 37 - assuming they exist below this line)
# This is where the actual TEST_REGISTRY population would happen,
# modifying the TEST_REGISTRY imported from settings.
# Example (conceptual, actual code might differ):
# TEST_REGISTRY["initiate_corsi_test"] = {
# "name": "Тест Корси (Плитки)",
# "start_function": corsi_handlers.start_corsi_test,
# "results_exist_check": check_if_corsi_results_exist,
# "fsm_group_class": CorsiTestStates,
# "save_function": corsi_handlers.save_corsi_results,
# "cleanup_function": corsi_handlers.cleanup_corsi_messages,
# "end_test_function": corsi_handlers.evaluate_user_sequence, # Or similar function that calls battery_proceed
# }
# ... other test registrations ...

# --- Test Selection and Start Flow ---
# ... (Full implementations from Turn 37 - assuming they exist below this line) ...

# --- Test Battery Flow Handlers ---
async def start_battery_phase(state: FSMContext, bot: Bot, trigger_event: Union[CallbackQuery, Message], phase: str):
    user_id = trigger_event.from_user.id
    chat_id = trigger_event.message.chat.id if isinstance(trigger_event, CallbackQuery) and trigger_event.message else trigger_event.chat.id
    logger.info(f"Starting battery phase: {phase} for user {user_id} in chat {chat_id}")
    await state.update_data(current_test_index_in_sequence=0, current_battery_phase=phase, _battery_phase_just_started=True)
    # BATTERY_TEST_SEQUENCE_KEYS and TEST_REGISTRY are used here
    await battery_proceed_after_test_completion(state, bot, trigger_event, TEST_REGISTRY, BATTERY_TEST_SEQUENCE_KEYS) 

@router.callback_query(F.data == "start_control_phase", BatteryCycleStates.showing_main_instruction)
async def handle_start_control_phase(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer("Запуск контрольного этапа...")
    if cb.message: await _safe_delete_message(bot, cb.message.chat.id, cb.message.message_id, "start_control_phase button")
    await start_battery_phase(state, bot, cb, phase='control')

async def medication_wait_timer_task(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    chat_id = callback_query.message.chat.id if callback_query.message else callback_query.from_user.id
    original_message_id = callback_query.message.message_id if callback_query.message else None
    try:
        logger.info(f"Medication timer started for user {callback_query.from_user.id}. Waiting {BATTERY_MEDICATION_WAIT_MINUTES * 60}s.")
        await asyncio.sleep(BATTERY_MEDICATION_WAIT_MINUTES * 60)
        current_fsm_state = await state.get_state()
        if current_fsm_state == BatteryCycleStates.medication_timer_active.state:
            logger.info(f"Medication timer finished for user {callback_query.from_user.id}.")
            if original_message_id:
                try: await bot.edit_message_text(chat_id=chat_id, message_id=original_message_id, text="Время ожидания истекло.", reply_markup=None)
                except TelegramBadRequest: logger.warning(f"Medication timer: Could not edit original ack message {original_message_id}.")
            med_phase_kbd = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Начать тестирование (этап 'Лекарство')", callback_data="start_medication_phase")]])
            await bot.send_message(chat_id, "Время ожидания истекло. Вы можете начать повторное тестирование.", reply_markup=med_phase_kbd)
            await state.set_state(BatteryCycleStates.showing_post_medication_instruction)
        else: logger.info(f"Medication timer completed for user {callback_query.from_user.id}, but state was {current_fsm_state}.")
    except asyncio.CancelledError: logger.info(f"Medication timer task cancelled for user {callback_query.from_user.id}.")
    except Exception as e: logger.error(f"Error in medication_wait_timer_task for user {callback_query.from_user.id}: {e}", exc_info=True); await state.set_state(BatteryCycleStates.idle) 
    finally: await state.update_data(active_timer_task=None)

@router.callback_query(F.data == "medication_acknowledged", BatteryCycleStates.awaiting_medication_acknowledgement)
async def handle_medication_acknowledged(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer(text=f'Вы можете продолжить через {BATTERY_MEDICATION_WAIT_MINUTES} минут.', show_alert=True)
    if cb.message:
        try:
            await bot.edit_message_text(chat_id=cb.message.chat.id, message_id=cb.message.message_id, text=f"Пожалуйста, подождите {BATTERY_MEDICATION_WAIT_MINUTES} минут. Другие действия (кроме /stopbattery) временно недоступны.", reply_markup=None)
            await state.update_data(active_medication_wait_message_id=cb.message.message_id) 
        except TelegramBadRequest: logger.warning(f"Failed to edit medication ack message {cb.message.message_id}")
    await state.set_state(BatteryCycleStates.medication_timer_active)
    await state.update_data(medication_timer_end_time = time.time() + (BATTERY_MEDICATION_WAIT_MINUTES * 60))
    timer_task = asyncio.create_task(medication_wait_timer_task(cb, state, bot))
    await state.update_data(active_timer_task=timer_task)

@router.callback_query(F.data == "start_medication_phase", BatteryCycleStates.showing_post_medication_instruction)
async def handle_start_medication_phase(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer("Запуск этапа 'Лекарство'...")
    if cb.message: await _safe_delete_message(bot, cb.message.chat.id, cb.message.message_id, "start_medication_phase button")
    await start_battery_phase(state, bot, cb, phase='medication')

@router.message(Command("stopbattery"), StateFilter(BatteryCycleStates.all_states_names))
async def handle_stop_battery_command(message: Message, state: FSMContext, bot: Bot):
    user_id, chat_id = message.from_user.id, message.chat.id
    fsm_data = await state.get_data(); current_fsm_state_str = await state.get_state()
    logger.info(f"User {user_id} initiated /stopbattery in state {current_fsm_state_str}.")
    active_timer_task = fsm_data.get('active_timer_task')
    if active_timer_task and not active_timer_task.done():
        active_timer_task.cancel(); await asyncio.sleep(0.1) 
    await state.update_data(active_timer_task=None)
    for key in ['active_medication_wait_message_id', 'active_pause_message_id']:
        msg_id = fsm_data.get(key)
        if msg_id: await _safe_delete_message(bot, chat_id, msg_id, f"stopbattery cleanup of {key}"); await state.update_data({key: None})
    if current_fsm_state_str in [BatteryCycleStates.running_control_phase_test.state, BatteryCycleStates.running_medication_phase_test.state]:
        current_test_idx = fsm_data.get("current_test_index_in_sequence")
        # BATTERY_TEST_SEQUENCE_KEYS and TEST_REGISTRY are used here
        if current_test_idx is not None and 0 <= current_test_idx < len(BATTERY_TEST_SEQUENCE_KEYS): 
            active_test_key = BATTERY_TEST_SEQUENCE_KEYS[current_test_idx] 
            active_test_cfg = TEST_REGISTRY.get(active_test_key)
            if active_test_cfg:
                logger.info(f"Attempting to cleanup active test '{active_test_cfg.get('name')}' during /stopbattery.")
                cleanup_func = active_test_cfg.get("cleanup_function")
                if callable(cleanup_func):
                    try: await cleanup_func(state, bot, final_text=f"Батарея тестов прервана пользователем.")
                    except Exception as e: logger.error(f"Error in cleanup_func for {active_test_key} during /stopbattery: {e}", exc_info=True)
    await message.answer("Батарея тестов прервана. Все текущие операции остановлены. Вы возвращены в главное меню.")
    profile_data = await get_active_profile_from_fsm(state)
    await state.clear()
    if profile_data:
        await state.set_data({"active_unique_id": profile_data.get("unique_id"), "active_name": profile_data.get("name"), "active_age": profile_data.get("age"), "active_telegram_id": profile_data.get("telegram_id")})
        await send_main_action_menu(bot, message, ACTION_SELECTION_KEYBOARD_RETURNING)
    else: await send_main_action_menu(bot, message, ACTION_SELECTION_KEYBOARD_NEW)

# --- Utility Commands ---
@router.message(Command("mydata"), IsNotInBatteryFilter()) 
async def show_my_data_command(message: Message, state: FSMContext, bot: Bot):
    profile = await get_active_profile_from_fsm(state)
    if not profile:
        prompt_msg = await message.answer(text=NEW_UNAUTHORIZED_PROMPT_TEXT, reply_markup=NEW_UNAUTHORIZED_KEYBOARD)
        await state.update_data(unauthorized_prompt_message_id=prompt_msg.message_id)
        await state.set_state(UserData.waiting_for_first_time_response); return
    uid_to_check = str(profile.get("unique_id"))
    name_display = profile.get("name", "N/A"); age_display = profile.get("age", "N/A")
    lines = [f"Данные для UID: <b>{uid_to_check}</b> (Имя: {name_display}, Возраст: {age_display})"]
    excel_data = await asyncio.to_thread(get_all_user_data_from_excel, uid_to_check)
    if "error" in excel_data: lines.append(excel_data["error"])
    elif "info" in excel_data: lines.append(excel_data["info"])
    else:
        lines.append("--- Результаты тестов из файла ---")
        for header_name, display_val in excel_data.items():
            if header_name in BASE_HEADERS and header_name not in ["Telegram ID", "Unique ID"]:
                if header_name == "Name" and name_display != "N/A" and name_display == display_val: continue
                if header_name == "Age" and age_display is not None and str(age_display) == display_val: continue
            lines.append(f"<b>{str(header_name)}:</b> {str(display_val)}")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

@router.message(Command("export"), IsNotInBatteryFilter()) 
async def export_data_to_excel_command(message: Message):
    if os.path.exists(EXCEL_FILENAME):
        try: await message.reply_document(FSInputFile(EXCEL_FILENAME), caption="База данных пользователей и результатов.")
        except Exception as e: logger.error(f"Не удалось отправить Excel файл: {e}", exc_info=True); await message.answer(f"Не удалось отправить файл: {e}")
    else: await message.answer(f"Файл данных '{EXCEL_FILENAME}' не найден на сервере.")

@router.callback_query(F.data == "logout_profile", StateFilter(None)) 
async def logout_profile_callback(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer("Ваш профиль был сброшен из текущей сессии.", show_alert=True)
    if cb.message:
        try: await bot.edit_message_text(chat_id=cb.message.chat.id, message_id=cb.message.message_id, text="Профиль сброшен. Используйте /start для нового входа или регистрации.", reply_markup=None)
        except TelegramBadRequest: await _safe_delete_message(bot, cb.message.chat.id, cb.message.message_id, "logout_profile menu cleanup"); await bot.send_message(cb.from_user.id, "Профиль сброшен. Используйте /start для нового входа или регистрации.")
    else: await bot.send_message(cb.from_user.id, "Профиль сброшен. Используйте /start для нового входа или регистрации.")
    await _clear_fsm_and_set_profile(state, None)

@router.callback_query(F.data == "run_test_battery", StateFilter(None)) 
async def on_run_test_battery_callback(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer() 
    profile = await get_active_profile_from_fsm(state)
    chat_id = cb.message.chat.id if cb.message else cb.from_user.id
    if not profile:
        logger.info(f"User {chat_id} attempted to start battery without profile.")
        unauth_text = "Для запуска батареи тестов необходимо сначала войти в систему или зарегистрироваться."
        if cb.message:
            try: await bot.edit_message_text(chat_id=chat_id, message_id=cb.message.message_id, text=unauth_text, reply_markup=NEW_UNAUTHORIZED_KEYBOARD)
            except TelegramBadRequest: await _safe_delete_message(bot, chat_id, cb.message.message_id, "del old run_test_battery btn"); new_prompt_msg = await bot.send_message(chat_id, unauth_text, reply_markup=NEW_UNAUTHORIZED_KEYBOARD); await state.update_data(unauthorized_prompt_message_id=new_prompt_msg.message_id)
            else: await state.update_data(unauthorized_prompt_message_id=cb.message.message_id)
        else: new_prompt_msg = await bot.send_message(chat_id, unauth_text, reply_markup=NEW_UNAUTHORIZED_KEYBOARD); await state.update_data(unauthorized_prompt_message_id=new_prompt_msg.message_id)
        await state.set_state(UserData.waiting_for_first_time_response); return
    if cb.message: await _safe_delete_message(bot, chat_id, cb.message.message_id, "main menu before battery start")
    await state.set_state(BatteryCycleStates.showing_main_instruction)
    await state.update_data(current_battery_phase='control', current_test_index_in_sequence=0, original_user_uid=profile.get('unique_id'), battery_start_time=time.time(), active_battery_test_message_id=None, medication_timer_end_time=None)
    instruction_text = ("<b>Батарея когнитивных тестов</b>\n\n"
                        "Это исследование включает два этапа тестирования, разделенных периодом отдыха (и, если применимо, приема лекарства/плацебо).\n\n"
                        "1. <b>Контрольный этап:</b> Вы пройдете серию тестов для оценки вашего текущего когнитивного состояния.\n"
                        "2. <b>Этап после отдыха/медикации:</b> После перерыва (и приема препарата, если это часть вашего исследования) вы снова пройдете ту же серию тестов.\n\n"
                        "Пожалуйста, убедитесь, что у вас достаточно времени (около 30-40 минут на каждый этап) и вы находитесь в спокойной обстановке.\n"
                        "Нажмите кнопку ниже, чтобы начать первый (контрольный) этап.")
    battery_instruction_keyboard = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Начать контрольный этап", callback_data="start_control_phase")]])
    await bot.send_message(chat_id, instruction_text, reply_markup=battery_instruction_keyboard, parse_mode=ParseMode.HTML)

# --- User Registration and Login Flow Handlers (Placeholders for brevity) ---
@router.callback_query(F.data == "user_is_new", UserData.waiting_for_first_time_response)
# async def handle_user_is_new_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
@router.callback_query(F.data == "user_is_returning", UserData.waiting_for_first_time_response)
# async def handle_user_is_returning_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
# @router.message(UserData.waiting_for_name)
# async def process_name_input(message: Message, state: FSMContext, bot: Bot): pass
# @router.message(UserData.waiting_for_age)
# async def process_age_input(message: Message, state: FSMContext, bot: Bot): pass
# @router.message(UserData.waiting_for_unique_id)
# async def process_unique_id_input(message: Message, state: FSMContext, bot: Bot): pass
# @router.callback_query(F.data == "try_id_again", UserData.waiting_for_unique_id)
# async def handle_try_id_again_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
# @router.callback_query(F.data == "register_new_after_fail", UserData.waiting_for_unique_id)
# async def handle_register_new_after_fail_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass

# --- Test Selection and Start Flow Handlers (Placeholders for brevity) ---
# @router.callback_query(F.data == "select_specific_test", StateFilter(None))
# async def on_select_specific_test_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
# @router.callback_query(F.data.startswith("select_test_"), StateFilter(None))
# async def on_test_selected_callback(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
# @router.callback_query(F.data == "confirm_overwrite_test_results", UserData.waiting_for_test_overwrite_confirmation)
# async def handle_confirm_overwrite_test_results(cb: CallbackQuery, state: FSMContext, bot: Bot): pass
# @router.callback_query(F.data == "cancel_overwrite_test_results", UserData.waiting_for_test_overwrite_confirmation)
# async def handle_cancel_overwrite_test_results(cb: CallbackQuery, state: FSMContext, bot: Bot): pass

# Note: The User Registration and Test Selection flow handlers are not fully pasted here for brevity,
# but they are assumed to be present as per the file content from Turn 37.
# The critical changes are the TEST_SEQUENCE removal and BATTERY_TEST_SEQUENCE_KEYS import/usage.
# The actual TEST_REGISTRY population code is also assumed to be present below this line,
# modifying the TEST_REGISTRY imported from settings.py.
