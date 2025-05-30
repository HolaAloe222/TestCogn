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
from openpyxl import load_workbook

from fsm_states import ShortTermMemoryStates, BatteryCycleStates
from settings import (
    STM_WORD_POOL,
    STM_NUM_WORDS_TO_SHOW,
    STM_DISPLAY_DURATION_S,
    STM_HEADERS,
    EXCEL_FILENAME,
    ALL_EXPECTED_HEADERS,  # For ensuring sheet structure on save
)
from utils.bot_helpers import get_active_profile_from_fsm

# Assuming common_handlers.proceed_in_battery will be created
# from ..common_handlers import proceed_in_battery # Adjusted import path

logger = logging.getLogger(__name__)
router = Router()
IKB = InlineKeyboardButton


async def start_stm_test(
        trigger_event: Union[Message, CallbackQuery],
        state: FSMContext,  # This is the main FSM context (BatteryCycleStates)
        profile: Dict[str, Any],
        bot_instance: Bot,
        # Battery specific parameters
        is_battery_mode: bool,
        battery_target_sheet: Optional[str],
        battery_user_uid: Optional[str]
):
    source_message = (
        trigger_event.message
        if isinstance(trigger_event, CallbackQuery)
        else trigger_event
    )
    chat_id = source_message.chat.id

    await state.set_state(ShortTermMemoryStates.showing_words_stm)

    words_to_show = random.sample(STM_WORD_POOL, STM_NUM_WORDS_TO_SHOW)

    stm_fsm_data = {
        "stm_chat_id": chat_id,
        "stm_presented_words": words_to_show,
        "stm_stimulus_message_id": None,
        "stm_is_battery_mode": is_battery_mode,
        "stm_battery_target_sheet": battery_target_sheet,
        "stm_battery_user_uid": battery_user_uid,
        "stm_start_time": time.time()  # For potential timing if needed later
    }
    await state.update_data(**stm_fsm_data)

    words_text = "Запомните слова:\n\n" + ", ".join(words_to_show)

    try:
        stimulus_msg = await bot_instance.send_message(chat_id, words_text)
        await state.update_data(stm_stimulus_message_id=stimulus_msg.message_id)

        # Start timer to hide words and ask for recall
        asyncio.create_task(
            stm_display_timer(state, bot_instance, chat_id, stimulus_msg.message_id, words_text)
        )
        logger.info(
            f"STM Test: Displaying {STM_NUM_WORDS_TO_SHOW} words for {STM_DISPLAY_DURATION_S}s to UID {battery_user_uid or profile.get('unique_id')}")

    except Exception as e:
        logger.error(f"STM Test: Error sending stimulus message: {e}", exc_info=True)
        await bot_instance.send_message(chat_id, "Ошибка при запуске теста на кратковременную память.")
        await _finish_stm_test(state, bot_instance, is_interrupted=True, error_occurred=True)


async def stm_display_timer(state: FSMContext, bot: Bot, chat_id: int, stimulus_msg_id: int, original_words_text: str):
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
        except TelegramBadRequest:  # Message might have been deleted or something went wrong
            logger.warning(f"STM Timer: Could not edit stimulus message {stimulus_msg_id}. Sending new prompt.")
            new_prompt_msg = await bot.send_message(
                chat_id,
                "Время вышло! Введите слова, которые Вы запомнили (одним сообщением, через запятую или пробел):"
            )
            # Update FSM with new message ID if necessary, or handle this if user interacts with old one
            # For simplicity, we assume user replies to the new one.
            # The original stm_stimulus_message_id in FSM might become stale for cleanup.
            # Let's update it to the prompt message for potential cleanup.
            await state.update_data(stm_stimulus_message_id=new_prompt_msg.message_id)

        await state.set_state(ShortTermMemoryStates.waiting_for_recall_stm)

    except asyncio.CancelledError:
        logger.info("STM display timer cancelled.")
    except Exception as e:
        logger.error(f"STM display timer error: {e}", exc_info=True)
        # Potentially try to recover or end the test gracefully
        current_fsm_state_str = await state.get_state()
        if current_fsm_state_str and current_fsm_state_str.startswith(ShortTermMemoryStates.__name__):
            await bot.send_message(chat_id, "Произошла ошибка с таймером. Пожалуйста, попробуйте ввести слова.")
            await state.set_state(ShortTermMemoryStates.waiting_for_recall_stm)  # Still allow input


@router.message(ShortTermMemoryStates.waiting_for_recall_stm, F.text)
async def handle_stm_recall_input(message: Message, state: FSMContext, bot: Bot):
    user_recalled_text = message.text
    # No immediate feedback on correctness as per spec ("Оценка правильности будет производиться ... вручную")

    await _finish_stm_test(state, bot, recalled_text=user_recalled_text, is_interrupted=False)


async def _finish_stm_test(
        state: FSMContext,
        bot_instance: Bot,
        recalled_text: Optional[str] = None,
        is_interrupted: bool = False,
        error_occurred: bool = False
):
    data = await state.get_data()
    chat_id = data.get("stm_chat_id")

    is_battery = data.get("stm_is_battery_mode", False)
    target_sheet = data.get("stm_battery_target_sheet")
    user_uid = data.get("stm_battery_user_uid")
    presented_words = data.get("stm_presented_words", [])

    actual_interrupted_status = is_interrupted or error_occurred

    if not user_uid and is_battery:  # Should not happen if battery mode is set up correctly
        logger.error("STM Finish: In battery mode but battery_user_uid is missing!")
        # Fallback to profile UID if possible, but this indicates a flow error
        profile = await get_active_profile_from_fsm(state)  # Uses main FSM state
        user_uid = profile.get("unique_id") if profile else None

    if user_uid:  # Only save if we have a UID
        await save_stm_results(
            user_uid=user_uid,
            target_sheet_name=target_sheet if is_battery else None,  # Pass None if not battery, save to default
            presented_words=presented_words,
            recalled_words_raw=recalled_text if recalled_text else "N/A (Прервано)",
            is_interrupted=actual_interrupted_status
        )
        if chat_id:
            result_msg = "Результаты теста на кратковременную память сохранены."
            if actual_interrupted_status:
                result_msg = "Тест на кратковременную память был прерван. Частичные данные сохранены."
            elif error_occurred:
                result_msg = "Тест на кратковременную память завершен с ошибкой. Данные сохранены."
            await bot_instance.send_message(chat_id, result_msg)
    else:
        logger.warning("STM Finish: No UID found, results not saved.")
        if chat_id:
            await bot_instance.send_message(chat_id,
                                            "Не удалось определить пользователя для сохранения результатов STM.")

    await cleanup_stm_ui(state, bot_instance)

    # Transitioning FSM states (main battery FSM)
    if is_battery:
        # This function needs to set the *main battery FSM state*
        # This implies 'state' parameter is the main FSM context
        current_battery_phase = data.get("current_battery_phase")  # This should be in main FSM data
        if current_battery_phase == "control":
            await state.set_state(BatteryCycleStates.control_phase_inter_test_pause)
        elif current_battery_phase == "medication":
            await state.set_state(BatteryCycleStates.medication_phase_inter_test_pause)
        else:  # Should not happen
            logger.error(f"STM Finish: Unknown battery phase: {current_battery_phase}. Setting to idle.")
            await state.set_state(BatteryCycleStates.idle)

        # The actual call to _start_inter_test_pause will be triggered by a handler for these new states
        # in common_handlers.py
        # For example, common_handlers.py will have:
        # @router.message(BatteryCycleStates.control_phase_inter_test_pause) or on state entry
        # async def on_enter_control_inter_test_pause(message: Message, state: FSMContext, bot: Bot):
        #     await _start_inter_test_pause(state, bot)
        # This means STM just sets the state, and the battery FSM drives the next step.
    else:
        # Non-battery mode: clear STM states, return to menu (handled by caller or common logic)
        await state.set_state(None)  # Clear STM specific state if not battery
        # The common_handlers.py logic for single tests would then take over.


async def save_stm_results(
        user_uid: str,
        target_sheet_name: Optional[str],  # If None, use default sheet (for non-battery mode)
        presented_words: List[str],
        recalled_words_raw: str,
        is_interrupted: bool
):
    logger.info(f"STM Save: UID={user_uid}, Sheet='{target_sheet_name or 'Default'}' Int={is_interrupted}")

    if not user_uid:
        logger.error("STM Save: user_uid is missing. Cannot save.")
        return

    try:
        if not os.path.exists(EXCEL_FILENAME):
            logger.error(f"STM Save: Excel file '{EXCEL_FILENAME}' not found.")
            # Attempt to initialize, though this should be done at bot start
            from utils.excel_handler import initialize_excel_file
            initialize_excel_file()
            if not os.path.exists(EXCEL_FILENAME):
                return

        wb = load_workbook(EXCEL_FILENAME)

        # Determine which sheet to use
        ws = None
        if target_sheet_name and target_sheet_name in wb.sheetnames:
            ws = wb[target_sheet_name]
        elif target_sheet_name:  # Sheet specified but doesn't exist (should be created by init)
            logger.warning(f"STM Save: Target sheet '{target_sheet_name}' not found, attempting to use active sheet.")
            ws = wb.active  # Fallback, though this might not be desired
        else:  # No target sheet name (e.g. standalone test)
            ws = wb.active

        if ws is None:  # Should not happen if logic above is correct
            logger.error(f"STM Save: Could not determine a worksheet for saving UID {user_uid}.")
            return

        header_row = [cell.value for cell in ws[1]]
        if not header_row or "Unique ID" not in header_row:
            logger.error(f"STM Save: Sheet '{ws.title}' has no headers or 'Unique ID' header is missing.")
            # Fallback: attempt to write to ALL_EXPECTED_HEADERS if sheet is empty,
            # but this is risky if sheet has different structure.
            # For now, we'll assume initialize_excel_file sets up sheets correctly.
            if not header_row and ws.max_row == 0:  # Sheet is completely empty
                ws.append(ALL_EXPECTED_HEADERS)  # Use the global full header list
                header_row = ALL_EXPECTED_HEADERS
            else:
                return  # Cannot proceed without proper headers

        uid_col_idx = -1
        try:
            uid_col_idx = header_row.index("Unique ID")
        except ValueError:
            logger.error(f"STM Save: 'Unique ID' header not found in sheet '{ws.title}'.")
            return

        target_row = -1
        for r_idx, row_vals in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if len(row_vals) > uid_col_idx and str(row_vals[uid_col_idx]) == str(user_uid):
                target_row = r_idx
                break

        if target_row == -1:  # UID not found, create new row
            # Try to get Name and Age from a profile if available (complex if not passed)
            # For now, just create a row with UID and STM data
            new_row_values = [""] * len(header_row)
            new_row_values[uid_col_idx] = user_uid
            # Potentially add Name, Age if passed or retrievable
            ws.append(new_row_values)
            target_row = ws.max_row
            logger.info(f"STM Save: New row created for UID {user_uid} in sheet '{ws.title}' at row {target_row}.")

        # Find columns for STM_HEADERS in the current sheet's header_row
        stm_col_indices = {}
        for header_key in STM_HEADERS:
            try:
                stm_col_indices[header_key] = header_row.index(header_key) + 1  # 1-based
            except ValueError:
                logger.warning(
                    f"STM Save: Header '{header_key}' not found in sheet '{ws.title}'. Data for this header will be skipped.")

        if "STM_Presented_Words" in stm_col_indices:
            ws.cell(row=target_row, column=stm_col_indices["STM_Presented_Words"]).value = ", ".join(presented_words)
        if "STM_Recalled_Words" in stm_col_indices:
            ws.cell(row=target_row, column=stm_col_indices["STM_Recalled_Words"]).value = recalled_words_raw
        if "STM_Interrupted" in stm_col_indices:
            ws.cell(row=target_row, column=stm_col_indices["STM_Interrupted"]).value = "Да" if is_interrupted else "Нет"

        wb.save(EXCEL_FILENAME)
        logger.info(f"STM results for UID {user_uid} saved successfully to sheet '{ws.title}'.")

    except Exception as e:
        logger.error(
            f"STM Save: Failed to save results for UID {user_uid} to sheet '{target_sheet_name or 'Default'}'. Error: {e}",
            exc_info=True)


async def cleanup_stm_ui(state: FSMContext, bot_instance: Bot, final_text: Optional[str] = None):
    data = await state.get_data()
    chat_id = data.get("stm_chat_id")
    stimulus_msg_id = data.get("stm_stimulus_message_id")

    if chat_id and stimulus_msg_id:
        try:
            if final_text:  # If stop command provided text
                await bot_instance.edit_message_text(
                    chat_id=chat_id,
                    message_id=stimulus_msg_id,
                    text=final_text,
                    reply_markup=None  # remove any buttons
                )
            else:  # Normal cleanup
                await bot_instance.delete_message(chat_id, stimulus_msg_id)
            logger.debug(f"STM Cleanup: Handled stimulus_msg_id: {stimulus_msg_id}")
        except TelegramBadRequest:
            logger.warning(f"STM Cleanup: Failed to delete/edit message {stimulus_msg_id} (already gone or no change).")
        except Exception as e:
            logger.error(f"STM Cleanup: Error handling message {stimulus_msg_id}: {e}")

    # Clear STM specific data from FSM state
    # The main battery FSM state is handled by _finish_stm_test or /stopbattery
    keys_to_clear_from_fsm = [
        "stm_chat_id", "stm_presented_words", "stm_stimulus_message_id",
        "stm_is_battery_mode", "stm_battery_target_sheet", "stm_battery_user_uid",
        "stm_start_time"
    ]
    current_fsm_data = await state.get_data()
    updated_fsm_data = {k: v for k, v in current_fsm_data.items() if k not in keys_to_clear_from_fsm}
    await state.set_data(updated_fsm_data)

    # If not in battery mode, clear ShortTermMemoryStates
    is_battery = data.get("stm_is_battery_mode", False)
    if not is_battery:
        await state.set_state(None)  # Clear STM state if it was a standalone run
    logger.info("STM Cleanup: UI and FSM data cleaned up.")

