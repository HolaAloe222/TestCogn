# filters/custom_filters.py
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

# Adjust the import path based on your project structure.
# If fsm_states is in the parent directory of 'filters', this might be:
# from ..fsm_states import BatteryCycleStates
# Or if 'filters' and 'fsm_states.py' are at the same root level of a package:
# from project_name.fsm_states import BatteryCycleStates 
# For now, assuming a direct import works if PYTHONPATH is configured or they are in the same top-level package.
from fsm_states import BatteryCycleStates

class IsNotInBatteryFilter(Filter):
    """
    Blocks commands (except /stopbattery) when the user is in an active test battery cycle.
    """
    async def __call__(self, message: Message, state: FSMContext) -> bool:
        current_fsm_state_str = await state.get_state()

        is_in_battery_cycle = False
        if current_fsm_state_str:
            # Check if the current state string starts with the group name of BatteryCycleStates
            # BatteryCycleStates.all_states_names might be more robust if available and includes group name prefix.
            # Example: "BatteryCycleStates:idle", "BatteryCycleStates:running_control_phase_test"
            # For aiogram 3.x, state objects have a .group attribute.
            # If BatteryCycleStates.states_group.name is accessible and gives "BatteryCycleStates"
            if hasattr(BatteryCycleStates, 'states_group') and hasattr(BatteryCycleStates.states_group, 'name'):
                if current_fsm_state_str.startswith(BatteryCycleStates.states_group.name):
                    is_in_battery_cycle = True
            elif current_fsm_state_str in BatteryCycleStates.all_states_names: # Fallback if group name check is tricky
                 is_in_battery_cycle = True


        if is_in_battery_cycle:
            if message.text and message.text.startswith('/'):
                # Allow /stopbattery and /restart (as /restart is a way to force stop everything)
                if message.text.lower() == '/stopbattery' or message.text.lower() == '/restart':
                    return True 
                else:
                    # User is in battery and tried another command
                    try:
                        await message.answer(
                            "Пожалуйста, сначала завершите или прервите текущую батарею тестов (команда /stopbattery)."
                        )
                    except Exception as e:
                        # Log error if sending message fails, but still block command
                        print(f"Error sending command block message in IsNotInBatteryFilter: {e}")
                    return False # Block other commands
        
        return True # Allow if not in battery cycle or not a command that should be blocked
