# handlers/battery_utils.py
import asyncio
import logging
from typing import Union, Optional, Dict, List 

from aiogram import Bot
from aiogram.enums import ParseMode 
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup 

# Assuming fsm_states.py, settings.py, utils directory, keyboards.py are accessible from the project root
from fsm_states import BatteryCycleStates
from settings import BATTERY_INTER_TEST_PAUSE_S 
from utils.excel_handler import find_user_profile_in_excel
from utils.bot_helpers import _safe_delete_message, _clear_fsm_and_set_profile, send_main_action_menu
from keyboards import ACTION_SELECTION_KEYBOARD_RETURNING, IKB 

logger = logging.getLogger(__name__)

async def battery_proceed_after_test_completion( # Renamed and signature changed
    state: FSMContext, 
    bot: Bot, 
    trigger_event: Union[CallbackQuery, Message],
    test_registry: Dict, 
    test_sequence: List[str]
):
    """
    Proceeds to the next test in the battery sequence, handles inter-test pauses, or phase/battery completion.
    This function is moved from common_handlers.py to resolve circular imports.
    TEST_REGISTRY and TEST_SEQUENCE are now passed as arguments.
    """
    fsm_data = await state.get_data()
    current_phase = fsm_data.get("current_battery_phase")
    current_index = fsm_data.get("current_test_index_in_sequence", 0) 
    original_uid = fsm_data.get("original_user_uid")
    
    chat_id = trigger_event.message.chat.id if isinstance(trigger_event, CallbackQuery) and trigger_event.message else trigger_event.chat.id
    
    current_index_for_next_test = current_index
    if fsm_data.get('_battery_phase_just_started', False):
        current_index_for_next_test = 0
        await state.update_data(_battery_phase_just_started=False)
    else:
        current_index_for_next_test += 1
    await state.update_data(current_test_index_in_sequence=current_index_for_next_test)

    profile = await asyncio.to_thread(find_user_profile_in_excel, original_uid, None)
    if not profile:
        logger.error(f"Battery: Critical error - Profile not found for UID {original_uid}.")
        await bot.send_message(chat_id, "Ошибка: Не удалось загрузить ваш профиль. Батарея тестов остановлена.")
        await state.set_state(BatteryCycleStates.idle)
        return

    if current_index_for_next_test < len(test_sequence): # Use parameter
        if current_index_for_next_test > 0: 
            pause_state_to_set = BatteryCycleStates.control_phase_inter_test_pause if current_phase == 'control' else BatteryCycleStates.medication_phase_inter_test_pause
            await state.set_state(pause_state_to_set)
            pause_msg = await bot.send_message(chat_id, f"Следующий тест начнется через {BATTERY_INTER_TEST_PAUSE_S} секунд...")
            await state.update_data(active_pause_message_id=pause_msg.message_id)
            for i in range(BATTERY_INTER_TEST_PAUSE_S, 0, -1):
                if await state.get_state() != pause_state_to_set.state: 
                    logger.info("Battery: Pause cancelled.")
                    await _safe_delete_message(bot, chat_id, pause_msg.message_id, "pause countdown cancelled")
                    await state.update_data(active_pause_message_id=None)
                    return
                try: await bot.edit_message_text(f"Следующий тест начнется через {i} секунд...", chat_id, pause_msg.message_id)
                except TelegramBadRequest: pass
                await asyncio.sleep(1)
            await _safe_delete_message(bot, chat_id, pause_msg.message_id, "pause countdown finished")
            await state.update_data(active_pause_message_id=None)
        
        test_key = test_sequence[current_index_for_next_test] # Use parameter
        test_config = test_registry.get(test_key) # Use parameter
        if not test_config or not callable(test_config.get("start_function")):
            logger.error(f"Battery: Misconfig for test key '{test_key}'. Skipping.")
            await battery_proceed_after_test_completion(state, bot, trigger_event, test_registry, test_sequence) # Recursive call
            return
        
        running_phase_state = BatteryCycleStates.running_control_phase_test if current_phase == 'control' else BatteryCycleStates.running_medication_phase_test
        await state.set_state(running_phase_state)
        start_func = test_config["start_function"]
        target_sheet_name = f"{current_phase}_Ц"
        try:
            if test_key == "initiate_stm_test": 
                 await start_func(
                    trigger_event=trigger_event, 
                    state=state, 
                    profile=profile, 
                    bot_instance=bot, 
                    is_battery_mode=True, 
                    battery_target_sheet=target_sheet_name, 
                    battery_user_uid=original_uid 
                )
            else: 
                await start_func(trigger_event, state, profile, bot)
        except Exception as e_start_test:
            logger.error(f"Battery: Error calling start_function for {test_key} (UID {original_uid}): {e_start_test}", exc_info=True)
            await bot.send_message(chat_id, f"Ошибка при запуске теста: {test_config['name']}. Пропускаем.")
            await battery_proceed_after_test_completion(state, bot, trigger_event, test_registry, test_sequence) # Recursive call
            
    else: 
        logger.info(f"Battery: Phase '{current_phase}' completed for UID {original_uid}.")
        if current_phase == 'control':
            await state.set_state(BatteryCycleStates.awaiting_medication_acknowledgement)
            med_ack_kbd = InlineKeyboardMarkup(inline_keyboard=[
                [IKB(text="Готово ✅", callback_data="medication_acknowledged")]
            ])
            await bot.send_message(
                chat_id, 
                "Контрольный этап завершен. Пожалуйста, следуйте инструкциям по приему препарата или отдыху. "
                "Нажмите 'Готово ✅' когда будете готовы начать следующий этап (обычно через 30 минут).",
                reply_markup=med_ack_kbd
            )
        elif current_phase == 'medication':
            await state.set_state(BatteryCycleStates.battery_completed_prompt_next_action)
            await bot.send_message(chat_id, "Батарея тестов полностью завершена! Спасибо за ваше участие.")
            await _clear_fsm_and_set_profile(state, profile) 
            
            msg_context = trigger_event.message if isinstance(trigger_event, CallbackQuery) and trigger_event.message else trigger_event
            if isinstance(msg_context, Message): 
                 await send_main_action_menu(bot, msg_context, ACTION_SELECTION_KEYBOARD_RETURNING)
            else: 
                 await bot.send_message(chat_id, "Выберите действие:", reply_markup=ACTION_SELECTION_KEYBOARD_RETURNING)
        else: 
             await state.set_state(BatteryCycleStates.idle)
