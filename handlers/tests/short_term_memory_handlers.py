import asyncio
import logging
import random
import time
import os
from typing import Union, Optional, Dict, Any, List

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from openpyxl import Workbook, load_workbook # Added Workbook for explicit creation if needed

from fsm_states import ShortTermMemoryStates, BatteryCycleStates
from settings import (
    STM_WORD_POOL,
    STM_NUM_WORDS_TO_SHOW,
    STM_DISPLAY_DURATION_S,
    STM_HEADERS,
    EXCEL_FILENAME,
    ALL_EXPECTED_HEADERS, 
    BASE_HEADERS # Added BASE_HEADERS
)
from utils.bot_helpers import get_active_profile_from_fsm
from handlers.common_handlers import battery_proceed_after_test_completion


logger = logging.getLogger(__name__)
router = Router()
IKB = InlineKeyboardButton


async def start_stm_test(
        trigger_event: Union[Message, CallbackQuery],
        state: FSMContext, 
        profile: Dict[str, Any],
        bot_instance: Bot,
        is_battery_mode: bool,
        battery_target_sheet: Optional[str],
        battery_user_uid: Optional[str] # This is the original_user_uid from battery
):
    source_message = (
        trigger_event.message
        if isinstance(trigger_event, CallbackQuery)
        else trigger_event
    )
    chat_id = source_message.chat.id

    await state.set_state(ShortTermMemoryStates.showing_words_stm)

    words_to_show = random.sample(STM_WORD_POOL, STM_NUM_WORDS_TO_SHOW)
    
    # Store all necessary info for saving later, including full profile from battery start
    # if in battery mode.
    stm_fsm_data = {
        "stm_chat_id": chat_id,
        "stm_presented_words": words_to_show,
        "stm_stimulus_message_id": None,
        "stm_is_battery_mode": is_battery_mode,
        "stm_battery_target_sheet": battery_target_sheet, # e.g. "контроль_Ц"
        "stm_battery_user_uid": battery_user_uid, # UID for saving
        "stm_profile_name": profile.get("name"), # Store for saving
        "stm_profile_age": profile.get("age"),
        "stm_profile_tgid": profile.get("telegram_id"),
        "stm_start_time": time.time() 
    }
    await state.update_data(**stm_fsm_data)

    words_text = "Запомните слова:\n\n" + ", ".join(words_to_show)

    try:
        stimulus_msg = await bot_instance.send_message(chat_id, words_text)
        await state.update_data(stm_stimulus_message_id=stimulus_msg.message_id)

        asyncio.create_task(
            stm_display_timer(state, bot_instance, chat_id, stimulus_msg.message_id)
        )
        logger.info(
            f"STM Test: Displaying {STM_NUM_WORDS_TO_SHOW} words for {STM_DISPLAY_DURATION_S}s to UID {battery_user_uid or profile.get('unique_id')}")

    except Exception as e:
        logger.error(f"STM Test: Error sending stimulus message: {e}", exc_info=True)
        await bot_instance.send_message(chat_id, "Ошибка при запуске теста на кратковременную память.")
        # Ensure trigger_event is passed for _finish_stm_test
        await _finish_stm_test(state, bot_instance, trigger_event, is_interrupted=True, error_occurred=True)


async def stm_display_timer(state: FSMContext, bot: Bot, chat_id: int, stimulus_msg_id: int):
    try:
        await asyncio.sleep(STM_DISPLAY_DURATION_S)
        current_fsm_state_str = await state.get_state()
        if current_fsm_state_str != ShortTermMemoryStates.showing_words_stm.state:
            logger.info("STM Timer: State changed during display, aborting timer action.")
            return

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=stimulus_msg_id,
                text="Время вышло! Введите слова, которые Вы запомнили (одним сообщением, через запятую или пробел):"
            )
        except TelegramBadRequest: 
            logger.warning(f"STM Timer: Could not edit stimulus message {stimulus_msg_id}. Sending new prompt.")
            new_prompt_msg = await bot.send_message(
                chat_id,
                "Время вышло! Введите слова, которые Вы запомнили (одним сообщением, через запятую или пробел):"
            )
            await state.update_data(stm_stimulus_message_id=new_prompt_msg.message_id)
        await state.set_state(ShortTermMemoryStates.waiting_for_recall_stm)
    except asyncio.CancelledError:
        logger.info("STM display timer cancelled.")
    except Exception as e:
        logger.error(f"STM display timer error: {e}", exc_info=True)
        current_fsm_state_str = await state.get_state()
        if current_fsm_state_str and current_fsm_state_str.startswith(ShortTermMemoryStates.__name__):
            await bot.send_message(chat_id, "Произошла ошибка с таймером. Пожалуйста, попробуйте ввести слова.")
            await state.set_state(ShortTermMemoryStates.waiting_for_recall_stm)


@router.message(ShortTermMemoryStates.waiting_for_recall_stm, F.text)
async def handle_stm_recall_input(message: Message, state: FSMContext, bot: Bot):
    user_recalled_text = message.text
    await _finish_stm_test(state, bot, message, recalled_text=user_recalled_text, is_interrupted=False)


async def _finish_stm_test(
        state: FSMContext,
        bot_instance: Bot,
        trigger_event: Union[Message, CallbackQuery], # Added to pass context
        recalled_text: Optional[str] = None,
        is_interrupted: bool = False,
        error_occurred: bool = False
):
    data = await state.get_data()
    chat_id = data.get("stm_chat_id")
    user_uid = data.get("stm_battery_user_uid") or data.get("unique_id_for_test") # Prefer battery UID

    # Get profile details stored at the start of the test
    p_name = data.get("stm_profile_name")
    p_age = data.get("stm_profile_age")
    p_tgid = data.get("stm_profile_tgid")
    
    is_battery = data.get("stm_is_battery_mode", False)
    # If in battery mode, target_sheet comes from battery context, else None for default sheet saving
    target_sheet = data.get("stm_battery_target_sheet") if is_battery else None
    
    presented_words = data.get("stm_presented_words", [])
    actual_interrupted_status = is_interrupted or error_occurred

    if not user_uid: 
        logger.error("STM Finish: Critical - UID is missing! Cannot determine user for saving.")
        # Attempt to get active profile as a last resort, though stm_battery_user_uid should be reliable
        active_profile = await get_active_profile_from_fsm(state)
        if active_profile:
            user_uid = active_profile.get("unique_id")
            p_name = active_profile.get("name", p_name)
            p_age = active_profile.get("age", p_age)
            p_tgid = active_profile.get("telegram_id", p_tgid)
            logger.info(f"STM Finish: Using UID {user_uid} from active_profile as fallback.")
        else:
            logger.error("STM Finish: Still no UID after fallback. Results will NOT be saved.")
            if chat_id: await bot_instance.send_message(chat_id, "Критическая ошибка: не удалось определить пользователя для сохранения результатов STM.")
            # Fall through to cleanup without saving

    if user_uid: 
        await save_stm_results(
            user_uid=str(user_uid), # Ensure string
            p_name=p_name, p_age=p_age, p_tgid=p_tgid, # Pass profile info
            target_sheet_name=target_sheet,
            presented_words=presented_words,
            recalled_words_raw=recalled_text if recalled_text else "N/A (Прервано)",
            is_interrupted=actual_interrupted_status
        )
        if chat_id: # Only send message if chat_id is known
            result_msg = "Результаты теста на кратковременную память сохранены."
            if actual_interrupted_status: result_msg = "Тест на кратковременную память был прерван. Частичные данные сохранены."
            elif error_occurred: result_msg = "Тест на кратковременную память завершен с ошибкой. Данные сохранены."
            await bot_instance.send_message(chat_id, result_msg)
    else: # Should only be reached if UID was not found even after fallback
        logger.warning("STM Finish: No UID found, results not saved (this log should be rare).")
        if chat_id: await bot_instance.send_message(chat_id, "Не удалось определить пользователя для сохранения результатов STM.")

    await cleanup_stm_ui(state, bot_instance)
    from utils.bot_helpers import _clear_fsm_and_set_profile, send_main_action_menu # Local import for clarity
    from keyboards import ACTION_SELECTION_KEYBOARD_RETURNING # Local import

    if is_battery:
        # The common_handlers.proceed_to_next_test_in_battery will handle state transitions
        # For STM, it's assumed that after _finish_stm_test, the battery flow continues from common_handlers
        # Make sure that the state is correctly set for common_handlers to pick up
        # This is typically done by the calling test handler in common_handlers
        # For STM, this _finish_stm_test is the end, so it needs to call the battery progression
        await battery_proceed_after_test_completion(state, bot_instance, trigger_event)
    else: # Standalone test run
        profile_to_set = await get_active_profile_from_fsm(state) # Get profile before clearing
        await _clear_fsm_and_set_profile(state, profile_to_set) # Resets state to None if profile exists
        if profile_to_set and chat_id:
             # Determine message context for send_main_action_menu
            msg_context = trigger_event if isinstance(trigger_event, Message) else trigger_event.message
            if msg_context: # Ensure msg_context is not None
                 await send_main_action_menu(bot_instance, msg_context, ACTION_SELECTION_KEYBOARD_RETURNING)
            else: # Fallback if msg_context cannot be determined (e.g. if trigger_event was a CB from a deleted msg)
                 await bot_instance.send_message(chat_id, "Тест завершен. Выберите действие:", reply_markup=ACTION_SELECTION_KEYBOARD_RETURNING)


async def save_stm_results(
        user_uid: str,
        p_name: Optional[str], 
        p_age: Optional[Union[int, str]], 
        p_tgid: Optional[Union[int, str]],
        target_sheet_name: Optional[str],
        presented_words: List[str],
        recalled_words_raw: str,
        is_interrupted: bool
):
    logger.info(f"STM Save: UID={user_uid}, Sheet='{target_sheet_name or 'Default Sheet'}', Name='{p_name}', Interrupted={is_interrupted}")

    if not user_uid: # Should be caught by caller, but defensive
        logger.error("STM Save: user_uid is missing. Cannot save.")
        return

    try:
        if not os.path.exists(EXCEL_FILENAME):
            logger.info(f"STM Save: Excel file '{EXCEL_FILENAME}' not found. Initializing...")
            from utils.excel_handler import initialize_excel_file # Local import
            initialize_excel_file() # This should create sheets with ALL_EXPECTED_HEADERS
            if not os.path.exists(EXCEL_FILENAME):
                logger.error(f"STM Save: Excel file '{EXCEL_FILENAME}' still not found after init attempt.")
                return

        wb = load_workbook(EXCEL_FILENAME)
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif wb.sheetnames: # Fallback to first sheet if target_sheet_name not specified or not found
            ws = wb[wb.sheetnames[0]]
            logger.warning(f"STM Save: Target sheet '{target_sheet_name}' not found or not specified. Using sheet '{ws.title}' for UID {user_uid}.")
        else: # Should not happen if initialize_excel_file ran and created sheets
            logger.error(f"STM Save: No sheets found in '{EXCEL_FILENAME}'. Attempting to create a default sheet.")
            ws = wb.create_sheet(title="Sheet1") # Create a fallback sheet
            ws.append(ALL_EXPECTED_HEADERS) # Add headers to this new fallback sheet
            
        sheet_headers = [cell.value for cell in ws[1]] if ws.max_row > 0 else []
        if not sheet_headers or "Unique ID" not in sheet_headers:
            logger.warning(f"STM Save: Sheet '{ws.title}' lacks headers or 'Unique ID'. Appending ALL_EXPECTED_HEADERS.")
            if ws.max_row > 0 : ws.delete_rows(1, ws.max_row) # Clear potentially incorrect first row
            ws.append(ALL_EXPECTED_HEADERS)
            sheet_headers = ALL_EXPECTED_HEADERS # Update sheet_headers variable

        header_to_idx_map = {header: idx for idx, header in enumerate(sheet_headers)}
        uid_col_idx = header_to_idx_map.get("Unique ID")

        if uid_col_idx is None: # Should have been caught by header check
            logger.error(f"STM Save: 'Unique ID' header still not found in sheet '{ws.title}' after checks. Cannot save.")
            return

        target_row = -1
        for r_idx, row_values_tuple in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if len(row_values_tuple) > uid_col_idx and str(row_values_tuple[uid_col_idx]) == str(user_uid):
                target_row = r_idx
                break
        
        if target_row == -1:
            new_row_data = [""] * len(sheet_headers)
            new_row_data[uid_col_idx] = user_uid
            ws.append(new_row_data)
            target_row = ws.max_row
            logger.info(f"STM Save: New row created for UID {user_uid} in sheet '{ws.title}' at row {target_row}.")

        # Populate/Update BASE_HEADERS (Name, Age, Telegram ID)
        for base_header in BASE_HEADERS:
            if base_header in header_to_idx_map:
                col_idx_for_base = header_to_idx_map[base_header]
                value_to_set = None
                if base_header == "Name": value_to_set = p_name
                elif base_header == "Age": value_to_set = p_age
                elif base_header == "Telegram ID": value_to_set = p_tgid
                elif base_header == "Unique ID": value_to_set = user_uid # Should already be there

                if value_to_set is not None: # Only write if we have a value
                    ws.cell(row=target_row, column=col_idx_for_base + 1).value = value_to_set
        
        # Populate STM_HEADERS
        for stm_header in STM_HEADERS:
            if stm_header in header_to_idx_map:
                col_idx_for_stm = header_to_idx_map[stm_header]
                value_to_set = ""
                if stm_header == "STM_Presented_Words": value_to_set = ", ".join(presented_words)
                elif stm_header == "STM_Recalled_Words": value_to_set = recalled_words_raw
                elif stm_header == "STM_Interrupted": value_to_set = "Да" if is_interrupted else "Нет"
                ws.cell(row=target_row, column=col_idx_for_stm + 1).value = value_to_set
            else:
                logger.warning(f"STM Save: Header '{stm_header}' not found in sheet '{ws.title}'. Data for this header will be skipped.")

        wb.save(EXCEL_FILENAME)
        logger.info(f"STM results for UID {user_uid} saved successfully to sheet '{ws.title}'.")

    except Exception as e:
        logger.error(f"STM Save: Failed to save results for UID {user_uid} to sheet '{target_sheet_name or 'Default'}'. Error: {e}", exc_info=True)


async def cleanup_stm_ui(state: FSMContext, bot_instance: Bot, final_text: Optional[str] = None):
    data = await state.get_data()
    chat_id = data.get("stm_chat_id")
    stimulus_msg_id = data.get("stm_stimulus_message_id")

    if chat_id and stimulus_msg_id:
        try:
            if final_text: 
                await bot_instance.edit_message_text(chat_id=chat_id, message_id=stimulus_msg_id, text=final_text, reply_markup=None)
            else: 
                await bot_instance.delete_message(chat_id, stimulus_msg_id)
            logger.debug(f"STM Cleanup: Handled stimulus_msg_id: {stimulus_msg_id}")
        except TelegramBadRequest:
            logger.warning(f"STM Cleanup: Failed to delete/edit message {stimulus_msg_id} (already gone or no change).")
        except Exception as e:
            logger.error(f"STM Cleanup: Error handling message {stimulus_msg_id}: {e}")

    keys_to_clear_from_fsm = [
        "stm_chat_id", "stm_presented_words", "stm_stimulus_message_id",
        "stm_is_battery_mode", "stm_battery_target_sheet", "stm_battery_user_uid",
        "stm_start_time", "stm_profile_name", "stm_profile_age", "stm_profile_tgid" # Clear profile info too
    ]
    current_fsm_data = await state.get_data()
    updated_fsm_data = {k: v for k, v in current_fsm_data.items() if k not in keys_to_clear_from_fsm}
    await state.set_data(updated_fsm_data)
    
    is_battery = data.get("stm_is_battery_mode", False)
    if not is_battery: # If not in battery mode, clear the specific STM state
        current_state_str = await state.get_state()
        if current_state_str and current_state_str.startswith(ShortTermMemoryStates.__name__):
            await state.set_state(None) 
    logger.info("STM Cleanup: UI and FSM data cleaned up.")
