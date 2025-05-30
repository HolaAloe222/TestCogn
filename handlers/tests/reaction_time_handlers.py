# handlers/tests/reaction_time_handlers.py
import asyncio
import logging
import random
import time
import os

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
    InputMediaPhoto,
    Message,
    Chat,
    User,
)
from aiogram.filters import StateFilter

from fsm_states import ReactionTimeTestStates, BatteryCycleStates # Added BatteryCycleStates
from settings import (
    ALL_EXPECTED_HEADERS,
    EXCEL_FILENAME,
    REACTION_TIME_IMAGE_POOL, 
    REACTION_TIME_MEMORIZATION_S,
    REACTION_TIME_STIMULUS_INTERVAL_S,
    REACTION_TIME_MAX_ATTEMPTS,
    REACTION_TIME_NUM_STIMULI_IN_SEQUENCE,
    REACTION_TIME_HEADERS, # Imported
    BASE_HEADERS # Imported
)
from utils.bot_helpers import (
    send_main_action_menu,
    get_active_profile_from_fsm,
    _clear_fsm_and_set_profile, # Added
    _safe_delete_message # Added
)
from handlers.common_handlers import battery_proceed_after_test_completion # For battery mode
from keyboards import ACTION_SELECTION_KEYBOARD_RETURNING

logger = logging.getLogger(__name__)
router = Router()
IKB = InlineKeyboardButton


async def _delete_common_status_message(
    state: FSMContext, bot_instance: Bot, chat_id: int | None = None
):
    """Helper to delete the common 'status_message_id_to_delete_later'."""
    data = await state.get_data()
    common_status_msg_id = data.get("status_message_id_to_delete_later")
    current_chat_id = data.get("rt_chat_id") or chat_id

    if common_status_msg_id and current_chat_id:
        try:
            await bot_instance.delete_message(current_chat_id, common_status_msg_id)
            logger.info(f"RT Common: Deleted common status message ID: {common_status_msg_id} in chat {current_chat_id}")
        except TelegramBadRequest:
            logger.warning(f"RT Common: Failed to delete common status message ID: {common_status_msg_id}, already deleted or inaccessible.")
        except Exception as e_del_common:
            logger.error(f"RT Common: Error deleting common status message ID {common_status_msg_id}: {e_del_common}")
        await state.update_data(status_message_id_to_delete_later=None)


async def _rt_go_to_main_menu_or_clear( state: FSMContext, trigger_message: Message, bot_instance: Bot ):
    """Navigates to the main menu or clears state if no profile. Called after all cleanups."""
    # This function is now more generic and relies on the main FSM state for battery vs single test
    current_fsm_main_state_str = await state.get_state() # This will be BatteryCycleState or None
    fsm_data = await state.get_data()
    profile_data = await get_active_profile_from_fsm(state) # Use helper to get active profile data
    
    is_battery_mode = current_fsm_main_state_str is not None and current_fsm_main_state_str.startswith(BatteryCycleStates.__name__)

    await cleanup_reaction_time_ui(state, bot_instance, final_text=None) # Ensure UI is clean
    await _delete_common_status_message(state, bot_instance, fsm_data.get("rt_chat_id"))


    if is_battery_mode:
        # If in battery, proceed_to_next_test_in_battery will be called by the test completion handler in common_handlers
        # This function's role in battery mode is primarily to ensure cleanup and allow battery_proceed_after_test_completion to take over
        logger.info(f"RT: Test ended in battery mode. UID: {profile_data.get('unique_id') if profile_data else 'N/A'}. Common_handlers will proceed.")
        # The state should already be set by the calling function in common_handlers or by the test itself
        # to an appropriate BatteryCycleStates state.
        # For RT, if it ends itself (e.g. max attempts), it calls this.
        # If stopped by /stoptest, common_handlers.stop_test_command_handler calls this.
        # We need to ensure that if RT test ends itself within a battery, it calls battery_proceed_after_test_completion
        # This is now handled in on_rt_retry_no and other places where the test ends by itself.

    elif profile_data: # Standalone mode, profile exists
        await _clear_fsm_and_set_profile(state, profile_data) # Keep profile, clear other states
        await send_main_action_menu(bot_instance, trigger_message, ACTION_SELECTION_KEYBOARD_RETURNING)
    else: # Standalone mode, no profile (should not happen if test required profile)
        if hasattr(trigger_message, 'chat') and trigger_message.chat:
            await trigger_message.answer("Профиль не активен. Пожалуйста, /start для начала.")
        await state.clear()


async def _rt_memorization_phase_task(state: FSMContext, bot_instance: Bot, trigger_msg_context: Message):
    """Handles the memorization phase countdown and transitions to reaction phase."""
    try:
        await asyncio.sleep(REACTION_TIME_MEMORIZATION_S)
        if await state.get_state() != ReactionTimeTestStates.memorization_display.state:
            logger.info("RT Memorization task: State changed, aborting.")
            return

        data = await state.get_data()
        chat_id = data.get("rt_chat_id")
        memo_msg_id = data.get("rt_memorization_image_message_id")

        if memo_msg_id and chat_id:
            try:
                await bot_instance.delete_message(chat_id=chat_id, message_id=memo_msg_id)
                await state.update_data(rt_memorization_image_message_id=None)
            except Exception: pass # Ignore if already deleted

        await _start_rt_reaction_phase(state, bot_instance, trigger_msg_context)
    except asyncio.CancelledError:
        logger.info("RT Memorization task cancelled.")
    except Exception as e:
        logger.error(f"RT Memorization task critical error: {e}", exc_info=True)
        data = await state.get_data()
        chat_id = data.get("rt_chat_id")
        if chat_id:
            await bot_instance.send_message(chat_id, "Произошла критическая ошибка в фазе запоминания. Тест прерван.")
        
        is_battery = data.get("current_battery_phase") is not None
        target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None
        await save_reaction_time_results(state, is_interrupted=True, status_override="Failed in memorization phase", target_sheet_name=target_sheet)
        await _rt_go_to_main_menu_or_clear(state, trigger_msg_context, bot_instance)


async def _start_rt_reaction_phase(state: FSMContext, bot_instance: Bot, trigger_msg_context: Message):
    """Sets up and starts the reaction stimulus display cycle."""
    await state.set_state(ReactionTimeTestStates.reaction_stimulus_display)
    data = await state.get_data()
    chat_id = data.get("rt_chat_id")
    target_image_path = data.get("rt_target_image_path")

    if not REACTION_TIME_IMAGE_POOL:
        logger.error("RT Start Reaction Phase: REACTION_TIME_IMAGE_POOL is empty! Cannot proceed.")
        if chat_id: await bot_instance.send_message(chat_id, "Ошибка конфигурации: пул изображений для теста пуст. Тест прерван.")
        await _handle_rt_attempt_failure(state, bot_instance, "Ошибка конфигурации: пул изображений пуст", trigger_msg_context)
        return

    distractors = [p for p in REACTION_TIME_IMAGE_POOL if p != target_image_path]
    random.shuffle(distractors)
    stimuli_sequence = [{"path": p, "is_target": False} for p in distractors[:REACTION_TIME_NUM_STIMULI_IN_SEQUENCE - 1]]
    if REACTION_TIME_NUM_STIMULI_IN_SEQUENCE > 0:
        target_insert_pos = random.randint(0, len(stimuli_sequence)) if stimuli_sequence else 0
        stimuli_sequence.insert(target_insert_pos, {"path": target_image_path, "is_target": True})
    
    if not stimuli_sequence and REACTION_TIME_NUM_STIMULI_IN_SEQUENCE > 0 : # Safeguard if logic error occurs
        stimuli_sequence.append({"path": target_image_path, "is_target": True})


    await state.update_data(rt_stimuli_sequence=stimuli_sequence, rt_current_stimulus_index=0, rt_target_displayed_time=None, rt_reacted_correctly_this_attempt=False, rt_reaction_stimulus_message_id=None, rt_target_missed_message_id=None)
    reaction_task = asyncio.create_task(_rt_reaction_cycle_task(state, bot_instance, trigger_msg_context))
    await state.update_data(rt_reaction_cycle_task=reaction_task)


async def _rt_reaction_cycle_task(state: FSMContext, bot_instance: Bot, trigger_msg_context: Message):
    """Manages the display of individual stimuli and waits for reactions or interval timeout."""
    try:
        data = await state.get_data()
        chat_id = data.get("rt_chat_id")
        stimuli_sequence = data.get("rt_stimuli_sequence", [])
        current_idx = data.get("rt_current_stimulus_index", 0)
        stimulus_msg_id = data.get("rt_reaction_stimulus_message_id")

        if current_idx >= len(stimuli_sequence):
            if data.get("rt_target_displayed_time") and not data.get("rt_reacted_correctly_this_attempt"):
                logger.info(f"RT UID {data.get('rt_unique_id_for_test', 'N/A')}: Target missed (end of sequence).")
                if chat_id:
                    target_missed_msg = await bot_instance.send_message(chat_id, "Вы пропустили целевое изображение.")
                    await state.update_data(rt_target_missed_message_id=target_missed_msg.message_id)
                await _handle_rt_attempt_failure(state, bot_instance, "Цель пропущена", trigger_msg_context)
            return

        current_stimulus = stimuli_sequence[current_idx]
        image_path, is_target = current_stimulus["path"], current_stimulus["is_target"]
        await state.update_data(rt_current_displayed_image_is_target=is_target)

        caption_text = "РЕАГИРОВАТЬ!"
        kbd = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="💥 РЕАГИРОВАТЬ! 💥", callback_data="rt_react_button_pressed")]])

        try:
            img_file = FSInputFile(image_path)
            if not stimulus_msg_id:
                msg = await bot_instance.send_photo(chat_id, photo=img_file, caption=caption_text, reply_markup=kbd)
                stimulus_msg_id = msg.message_id
                await state.update_data(rt_reaction_stimulus_message_id=stimulus_msg_id)
            else:
                media = InputMediaPhoto(media=img_file, caption=caption_text)
                await bot_instance.edit_message_media(chat_id=chat_id, message_id=stimulus_msg_id, media=media, reply_markup=kbd)
            
            if is_target:
                await state.update_data(rt_target_displayed_time=time.time())
        except Exception as e:
            logger.error(f"RT Reaction Cycle: Failed to send/edit stimulus image {image_path}: {e}", exc_info=True)
            if chat_id: await bot_instance.send_message(chat_id, "Ошибка отображения стимула. Попытка прервана.")
            await _handle_rt_attempt_failure(state, bot_instance, "Ошибка отображения стимула", trigger_msg_context)
            return

        await state.update_data(rt_current_stimulus_index=current_idx + 1)
        start_sleep = time.time()
        while time.time() - start_sleep < REACTION_TIME_STIMULUS_INTERVAL_S:
            await asyncio.sleep(0.05)
            if await state.get_state() != ReactionTimeTestStates.reaction_stimulus_display.state:
                logger.info("RT Reaction Cycle: State changed during stimulus display interval, aborting cycle.")
                return
        
        if await state.get_state() == ReactionTimeTestStates.reaction_stimulus_display.state:
            new_reaction_task = asyncio.create_task(_rt_reaction_cycle_task(state, bot_instance, trigger_msg_context))
            await state.update_data(rt_reaction_cycle_task=new_reaction_task)

    except asyncio.CancelledError: logger.info("RT Reaction cycle task cancelled.")
    except Exception as e:
        logger.error(f"RT Reaction cycle task critical error: {e}", exc_info=True)
        data = await state.get_data()
        chat_id = data.get("rt_chat_id")
        if chat_id: await bot_instance.send_message(chat_id, "Произошла критическая ошибка в фазе реакции. Тест прерван.")
        
        is_battery = data.get("current_battery_phase") is not None
        target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None
        await save_reaction_time_results(state, is_interrupted=True, status_override="Failed in reaction cycle (critical)", target_sheet_name=target_sheet)
        await _rt_go_to_main_menu_or_clear(state, trigger_msg_context, bot_instance)


async def _handle_rt_attempt_failure(state: FSMContext, bot_instance: Bot, reason: str, trigger_msg_context: Message):
    data = await state.get_data()
    current_attempt = data.get("rt_current_attempt", 1)
    chat_id = data.get("rt_chat_id")

    reaction_cycle_task = data.get("rt_reaction_cycle_task")
    if reaction_cycle_task and not reaction_cycle_task.done():
        reaction_cycle_task.cancel(); await asyncio.sleep(0.1) # Allow cancellation
    await state.update_data(rt_reaction_cycle_task=None)

    for key in ["rt_target_missed_message_id", "rt_reaction_stimulus_message_id"]:
        msg_id = data.get(key)
        if msg_id and chat_id:
            try: await bot_instance.delete_message(chat_id, msg_id)
            except: pass # Ignore if already deleted
        await state.update_data({key: None})

    current_attempt += 1
    await state.update_data(rt_current_attempt=current_attempt)
    
    is_battery = data.get("current_battery_phase") is not None
    target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None

    if current_attempt <= REACTION_TIME_MAX_ATTEMPTS:
        await state.set_state(ReactionTimeTestStates.awaiting_retry_confirmation)
        retry_text = (f"Причина: {reason}. Попытка {current_attempt - 1} из {REACTION_TIME_MAX_ATTEMPTS} не удалась.\n"
                      f"Хотите попробовать еще раз (осталось {REACTION_TIME_MAX_ATTEMPTS - (current_attempt - 1)} попыток)?")
        kbd = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Да, попробовать снова", callback_data="rt_retry_yes")], [IKB(text="Нет, завершить тест", callback_data="rt_retry_no")]])
        if chat_id:
            try:
                retry_prompt_msg = await bot_instance.send_message(chat_id, retry_text, reply_markup=kbd)
                await state.update_data(rt_retry_confirmation_message_id=retry_prompt_msg.message_id)
            except Exception as e_send_retry_prompt:
                logger.error(f"RT Handle Failure: Failed to send retry prompt: {e_send_retry_prompt}", exc_info=True)
                await save_reaction_time_results(state, is_interrupted=True, status_override="UI error on retry prompt", target_sheet_name=target_sheet)
                await _rt_go_to_main_menu_or_clear(state, trigger_msg_context, bot_instance)
    else:
        logger.info(f"RT UID {data.get('rt_unique_id_for_test', 'N/A')}: Max attempts reached. Reason: {reason}.")
        await state.update_data(rt_status="Failed")
        if chat_id: await bot_instance.send_message(chat_id, f"Причина: {reason}. Максимум попыток ({REACTION_TIME_MAX_ATTEMPTS}) исчерпано. Тест не пройден.")
        await save_reaction_time_results(state, is_interrupted=False, target_sheet_name=target_sheet) # Test ended due to exhaustion
        await _rt_go_to_main_menu_or_clear(state, trigger_msg_context, bot_instance)


async def start_reaction_time_test(trigger_event: Message | CallbackQuery, state: FSMContext, profile: dict, bot_instance: Bot):
    logger.info(f"Starting Reaction Time Test for UID: {profile.get('unique_id', 'N/A')}")
    msg_ctx = trigger_event.message if isinstance(trigger_event, CallbackQuery) else trigger_event
    chat_id = msg_ctx.chat.id

    await state.set_state(ReactionTimeTestStates.initial_instructions)
    await state.update_data(
        rt_unique_id_for_test=profile.get("unique_id"), rt_profile_name_for_test=profile.get("name"),
        rt_profile_age_for_test=profile.get("age"), rt_profile_telegram_id_for_test=profile.get("telegram_id"),
        rt_chat_id=chat_id, rt_current_attempt=1, rt_reaction_time_ms=None, rt_status="Pending",
        rt_target_image_path=None, rt_instruction_message_id=None, rt_memorization_image_message_id=None,
        rt_reaction_stimulus_message_id=None, rt_retry_confirmation_message_id=None, rt_target_missed_message_id=None,
        rt_current_displayed_image_is_target=False, rt_target_displayed_time=None, rt_reacted_correctly_this_attempt=False,
        rt_stimuli_sequence=[], rt_current_stimulus_index=0, rt_memorization_task=None, rt_reaction_cycle_task=None,
    )
    instruction_text = ("<b>Тест на Скорость Реакции</b>\n\n"
                        "1. Сначала вам будет показано изображение-цель на 10 секунд. Запомните его.\n"
                        "2. Затем изображение-цель исчезнет.\n"
                        "3. После этого начнут появляться другие изображения. Среди них один раз появится ваше целевое изображение.\n"
                        "4. Ваша задача – как можно быстрее нажать кнопку 'РЕАГИРОВАТЬ!', как только увидите целевое изображение.\n"
                        "   Если вы нажмете на кнопку, когда показано НЕ целевое изображение, это будет считаться ошибкой.\n\n"
                        f"У вас будет {REACTION_TIME_MAX_ATTEMPTS} попытки. Тест начнется после нажатия кнопки 'Начать'.")
    kbd = InlineKeyboardMarkup(inline_keyboard=[[IKB(text="Начать Тест", callback_data="rt_ack_instructions")]])
    try:
        instr_msg = await bot_instance.send_message(chat_id, instruction_text, reply_markup=kbd, parse_mode=ParseMode.HTML)
        await state.update_data(rt_instruction_message_id=instr_msg.message_id)
    except Exception as e:
        logger.error(f"RT start_reaction_time_test: Error sending initial instructions: {e}", exc_info=True)
        await bot_instance.send_message(chat_id, "Ошибка при запуске теста на Скорость Реакции. Попробуйте снова.")
        await state.clear() # Clear all FSM data on critical error


@router.callback_query(F.data == "rt_ack_instructions", ReactionTimeTestStates.initial_instructions)
async def rt_on_instructions_acknowledged(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    chat_id = data.get("rt_chat_id")
    instruction_msg_id = data.get("rt_instruction_message_id")

    if instruction_msg_id and chat_id:
        try:
            await bot.edit_message_text("Подготовка к фазе запоминания...", chat_id=chat_id, message_id=instruction_msg_id, reply_markup=None)
        except Exception: # If edit fails, delete and send new
            await _safe_delete_message(bot, chat_id, instruction_msg_id, "RT Ack Instr Prep Edit Fail") # Use common helper
            new_prep_msg = await bot.send_message(chat_id, "Подготовка к фазе запоминания...")
            await state.update_data(rt_instruction_message_id=new_prep_msg.message_id) # Update to new msg id
            
    await state.set_state(ReactionTimeTestStates.memorization_display)
    if not REACTION_TIME_IMAGE_POOL:
        logger.error("RT Ack Instr: REACTION_TIME_IMAGE_POOL is empty! Aborting test.")
        if chat_id: await bot.send_message(chat_id, "Ошибка конфигурации: Пул изображений пуст. Тест не может начаться.")
        await _rt_go_to_main_menu_or_clear(state, cb.message, bot)
        return

    target_image_path = random.choice(REACTION_TIME_IMAGE_POOL)
    await state.update_data(rt_target_image_path=target_image_path)
    
    try:
        img_file = FSInputFile(target_image_path)
        memo_img_msg = await bot.send_photo(chat_id=chat_id, photo=img_file, caption=f"Запомните это изображение! (Исчезнет через {REACTION_TIME_MEMORIZATION_S} сек)")
        await state.update_data(rt_memorization_image_message_id=memo_img_msg.message_id)
    except Exception as e_send_memo_img:
        logger.error(f"RT Ack Instr: Failed to send memorization image '{target_image_path}': {e_send_memo_img}", exc_info=True)
        if chat_id: await bot.send_message(chat_id, "Ошибка: не удалось загрузить изображение для запоминания. Тест прерван.")
        is_battery = data.get("current_battery_phase") is not None
        target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None
        await save_reaction_time_results(state, is_interrupted=True, status_override="Failed to send memorization image", target_sheet_name=target_sheet)
        await _rt_go_to_main_menu_or_clear(state, cb.message, bot)
        return

    memo_task = asyncio.create_task(_rt_memorization_phase_task(state, bot, cb.message))
    await state.update_data(rt_memorization_task=memo_task)


@router.callback_query(F.data == "rt_react_button_pressed", ReactionTimeTestStates.reaction_stimulus_display)
async def on_rt_react_button_pressed(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()

    reaction_cycle_task = data.get("rt_reaction_cycle_task")
    if reaction_cycle_task and not reaction_cycle_task.done():
        reaction_cycle_task.cancel(); await asyncio.sleep(0.1) # Allow cancellation
    await state.update_data(rt_reaction_cycle_task=None)

    chat_id = data.get("rt_chat_id")
    is_target_displayed_now = data.get("rt_current_displayed_image_is_target", False)
    target_display_time = data.get("rt_target_displayed_time")
    uid_for_test = data.get('original_user_uid') or data.get('rt_unique_id_for_test', 'N/A') # Prefer battery UID

    is_battery = data.get("current_battery_phase") is not None
    target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None

    if is_target_displayed_now and target_display_time:
        reaction_time_seconds = time.time() - target_display_time
        corrected_reaction_time_seconds = reaction_time_seconds - 0.350 # Telegram latency
        if corrected_reaction_time_seconds < 0: corrected_reaction_time_seconds = 0.001
        reaction_time_ms = int(corrected_reaction_time_seconds * 1000)

        await state.update_data(rt_reaction_time_ms=reaction_time_ms, rt_status="Passed", rt_reacted_correctly_this_attempt=True)
        logger.info(f"RT UID {uid_for_test}: Correct reaction. Raw RT: {reaction_time_seconds * 1000:.0f}ms. Corrected RT: {reaction_time_ms}ms.")
        
        stimulus_msg_id = data.get("rt_reaction_stimulus_message_id")
        if stimulus_msg_id and chat_id:
            try: await bot.delete_message(chat_id, stimulus_msg_id); await state.update_data(rt_reaction_stimulus_message_id=None)
            except: pass 
        if chat_id: await bot.send_message(chat_id, f"<b>Верно!</b> Ваше время реакции: {reaction_time_ms} мс.", parse_mode=ParseMode.HTML)

        await save_reaction_time_results(state, is_interrupted=False, target_sheet_name=target_sheet)
        await _rt_go_to_main_menu_or_clear(state, cb.message, bot) # This will also call cleanup_reaction_time_ui
    else:
        logger.info(f"RT UID {uid_for_test}: Incorrect reaction.")
        if chat_id: await bot.send_message(chat_id, "<b>Ошибка!</b> Вы нажали на кнопку, когда было показано НЕ целевое изображение, или реакция была за пределами допустимого окна.", parse_mode=ParseMode.HTML)
        await _handle_rt_attempt_failure(state, bot, "Неверная реакция", cb.message)


@router.callback_query(F.data == "rt_retry_yes", ReactionTimeTestStates.awaiting_retry_confirmation)
async def on_rt_retry_yes(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    chat_id = data.get("rt_chat_id")
    retry_msg_id = data.get("rt_retry_confirmation_message_id")

    if retry_msg_id and chat_id:
        try:
            await bot.edit_message_text("Готовим новую попытку...", chat_id=chat_id, message_id=retry_msg_id, reply_markup=None)
            await state.update_data(rt_instruction_message_id=retry_msg_id, rt_retry_confirmation_message_id=None)
        except Exception: # If edit fails, delete and send new
            await _safe_delete_message(bot, chat_id, retry_msg_id, "RT Retry Yes Edit Fail")
            new_prep_msg = await bot.send_message(chat_id, "Готовим новую попытку...")
            await state.update_data(rt_instruction_message_id=new_prep_msg.message_id, rt_retry_confirmation_message_id=None)
            
    await state.update_data(rt_target_image_path=None, rt_memorization_image_message_id=None, rt_reaction_stimulus_message_id=None,
                            rt_target_displayed_time=None, rt_reacted_correctly_this_attempt=False, rt_stimuli_sequence=[],
                            rt_current_stimulus_index=0, rt_target_missed_message_id=None, rt_memorization_task=None, rt_reaction_cycle_task=None)
    await state.set_state(ReactionTimeTestStates.initial_instructions) # Go back to start of test flow for this attempt
    await rt_on_instructions_acknowledged(cb, state, bot)


@router.callback_query(F.data == "rt_retry_no", ReactionTimeTestStates.awaiting_retry_confirmation)
async def on_rt_retry_no(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    chat_id = data.get("rt_chat_id")
    retry_msg_id = data.get("rt_retry_confirmation_message_id")

    if retry_msg_id and chat_id:
        try: await bot.delete_message(chat_id=chat_id, message_id=retry_msg_id); await state.update_data(rt_retry_confirmation_message_id=None)
        except: pass
    
    await state.update_data(rt_status="Failed (user declined retry)")
    if chat_id: await bot.send_message(chat_id, "Тест завершен по вашему выбору (не пройден).")

    is_battery = data.get("current_battery_phase") is not None
    target_sheet = data.get("current_battery_phase") + "_Ц" if is_battery and data.get("current_battery_phase") else None
    await save_reaction_time_results(state, is_interrupted=False, target_sheet_name=target_sheet)
    await _rt_go_to_main_menu_or_clear(state, cb.message, bot)


async def save_reaction_time_results(
    state: FSMContext, is_interrupted: bool = False,
    status_override: str = None, target_sheet_name: Optional[str] = None,
):
    data = await state.get_data()
    uid = data.get("original_user_uid") or data.get("rt_unique_id_for_test") # Prefer battery UID
    
    profile_info = await get_active_profile_from_fsm(state)
    p_name, p_age, p_tgid = "", "", ""
    if profile_info and profile_info.get('unique_id') == uid:
        p_name, p_age, p_tgid = profile_info.get("name"), profile_info.get("age"), profile_info.get("telegram_id")
    else:
        p_name, p_age, p_tgid = data.get("rt_profile_name_for_test"), data.get("rt_profile_age_for_test"), data.get("rt_profile_telegram_id_for_test")

    if not uid:
        logger.warning("RT Save Results: UID not found. Cannot save.")
        return

    time_ms = data.get("rt_reaction_time_ms")
    attempts = data.get("rt_current_attempt", 1)
    final_status = status_override or data.get("rt_status", "Unknown")
    
    if is_interrupted and final_status not in ["Passed", "Failed", "Interrupted by user"]:
        final_status = "Interrupted by user"
    interrupted_col_val = "Да" if final_status == "Interrupted by user" else "Нет"

    logger.info(f"RT Save: UID={uid}, Status={final_status}, TimeMs={time_ms}, Attempts={attempts}, Interrupted={interrupted_col_val}, Sheet='{target_sheet_name or 'default'}'")

    from openpyxl import load_workbook # Local import
    try:
        if not os.path.exists(EXCEL_FILENAME): initialize_excel_file() # Ensure file and sheets exist
        wb = load_workbook(EXCEL_FILENAME)
        
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames: ws = wb[target_sheet_name]
        elif wb.sheetnames: ws = wb[wb.sheetnames[0]]; logger.warning(f"RT Save: Target sheet '{target_sheet_name}' not found/specified. Using '{ws.title}'.")
        else: logger.error(f"RT Save: No sheets in '{EXCEL_FILENAME}'."); return
            
        sheet_headers = [cell.value for cell in ws[1]] if ws.max_row > 0 else []
        if not sheet_headers or "Unique ID" not in sheet_headers : # Should be ALL_EXPECTED_HEADERS
            logger.error(f"RT Save: Sheet '{ws.title}' lacks headers or 'Unique ID'. Re-initializing.");
            initialize_excel_file(); wb = load_workbook(EXCEL_FILENAME) # Reload after re-init
            if target_sheet_name and target_sheet_name in wb.sheetnames: ws = wb[target_sheet_name]
            else: ws = wb[wb.sheetnames[0]]
            sheet_headers = [cell.value for cell in ws[1]]
            if not sheet_headers or "Unique ID" not in sheet_headers:
                raise ValueError(f"Sheet '{ws.title}' still lacks 'Unique ID' after re-init.")

        header_to_idx_map = {header: idx for idx, header in enumerate(sheet_headers)}
        uid_col_idx = header_to_idx_map.get("Unique ID")
        
        row_num = -1
        if uid_col_idx is not None:
            for idx, row_vals_tuple in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if len(row_vals_tuple) > uid_col_idx and str(row_vals_tuple[uid_col_idx]) == str(uid):
                    row_num = idx; break
        
        if row_num == -1:
            new_row_data = [""] * len(sheet_headers)
            if uid_col_idx is not None: new_row_data[uid_col_idx] = uid
            ws.append(new_row_data); row_num = ws.max_row
            logger.info(f"RT Save: New row for UID {uid} in sheet '{ws.title}' at row {row_num}.")

        # Populate BASE_HEADERS & REACTION_TIME_HEADERS
        for header_key in BASE_HEADERS + REACTION_TIME_HEADERS:
            if header_key in header_to_idx_map:
                col_idx = header_to_idx_map[header_key]
                current_val_in_sheet = ws.cell(row=row_num, column=col_idx + 1).value
                value_to_write = None
                
                if header_key == "Name": value_to_write = p_name
                elif header_key == "Age": value_to_write = p_age
                elif header_key == "Telegram ID": value_to_write = p_tgid
                elif header_key == "Unique ID": value_to_write = uid
                elif header_key == "ReactionTime_Time_ms": value_to_write = time_ms if time_ms is not None else "N/A"
                elif header_key == "ReactionTime_Attempts": value_to_write = attempts
                elif header_key == "ReactionTime_Status": value_to_write = final_status
                elif header_key == "ReactionTime_Interrupted": value_to_write = interrupted_col_val
                
                if value_to_write is not None and (current_val_in_sheet is None or str(current_val_in_sheet) != str(value_to_write)):
                    ws.cell(row=row_num, column=col_idx + 1).value = value_to_write
            else:
                logger.warning(f"RT Save: Header '{header_key}' not found in sheet '{ws.title}'.")
        
        wb.save(EXCEL_FILENAME)
        logger.info(f"RT results for UID {uid} saved to sheet '{ws.title}'.")
    except Exception as e:
        logger.error(f"RT Save Results: Error for UID {uid}, Sheet '{target_sheet_name or 'default'}': {e}", exc_info=True)


async def cleanup_reaction_time_ui(state: FSMContext, bot_instance: Bot, final_text: str | None):
    data = await state.get_data()
    chat_id = data.get("rt_chat_id")
    logger.info(f"RT Cleanup UI: Chat {chat_id if chat_id else 'N/A'}. Final text: '{final_text}'")

    for task_key in ["rt_memorization_task", "rt_reaction_cycle_task"]:
        task = data.get(task_key)
        if task and not task.done(): task.cancel(); await asyncio.sleep(0.1)
        await state.update_data(**{task_key: None})

    rt_message_keys = ["rt_instruction_message_id", "rt_memorization_image_message_id", 
                       "rt_reaction_stimulus_message_id", "rt_retry_confirmation_message_id", 
                       "rt_target_missed_message_id"]
    
    ids_to_delete = {data.get(key) for key in rt_message_keys if data.get(key)}
    
    last_msg_id_for_edit = None
    if final_text: # Determine which message to edit if final_text is provided
        for key in reversed(rt_message_keys): # Prefer editing later messages
            if data.get(key): last_msg_id_for_edit = data.get(key); break
    
    if chat_id:
        if final_text and last_msg_id_for_edit:
            is_photo = last_msg_id_for_edit in [data.get("rt_memorization_image_message_id"), data.get("rt_reaction_stimulus_message_id")]
            try:
                if is_photo: await bot_instance.edit_message_caption(chat_id=chat_id, message_id=last_msg_id_for_edit, caption=final_text, reply_markup=None)
                else: await bot_instance.edit_message_text(text=final_text, chat_id=chat_id, message_id=last_msg_id_for_edit, reply_markup=None)
                ids_to_delete.discard(last_msg_id_for_edit) # Don't delete the edited message
            except Exception: # If edit fails, send new and delete all old
                try: await bot_instance.send_message(chat_id, final_text)
                except: pass # Ignore if send also fails
        elif final_text: # No specific message to edit, but final_text needs to be sent
             try: await bot_instance.send_message(chat_id, final_text)
             except: pass

        for msg_id in ids_to_delete: # Delete remaining messages
            try: await bot_instance.delete_message(chat_id, msg_id)
            except: pass # Ignore if already deleted or other error

    # Clean FSM: remove all "rt_" prefixed keys
    fsm_keys_to_remove = [key for key in data if key.startswith("rt_")]
    for key_to_remove in fsm_keys_to_remove: del data[key_to_remove] # Modify copy
    await state.set_data(data) # Set cleaned data back
    logger.info(f"RT Cleanup UI: FSM data cleaned of rt_ prefixed keys. Chat: {chat_id or 'N/A'}")
