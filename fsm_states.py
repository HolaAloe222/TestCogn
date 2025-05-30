from aiogram.fsm.state import StatesGroup, State


class UserData(StatesGroup):
    waiting_for_first_time_response = State()
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_unique_id = State()
    waiting_for_test_overwrite_confirmation = State()


class CorsiTestStates(StatesGroup):
    showing_sequence = State()
    waiting_for_user_sequence = State()


class StroopTestStates(StatesGroup):
    initial_instructions = State()
    part1_stimulus_response = State()
    part2_instructions = State()
    part2_stimulus_response = State()
    part3_instructions = State()
    part3_stimulus_response = State()


class ReactionTimeTestStates(StatesGroup):
    initial_instructions = State()
    memorization_display = State()
    reaction_stimulus_display = State()
    awaiting_retry_confirmation = State()


class VerbalFluencyStates(StatesGroup):
    showing_instructions_and_task = State()
    collecting_words = State()


class MentalRotationStates(StatesGroup):
    initial_instructions_mr = State()
    displaying_stimulus_mr = State()
    processing_answer_mr = State()
    inter_iteration_countdown_mr = State()


class RavenMatricesStates(StatesGroup):
    initial_instructions_raven = State()
    displaying_task_raven = State()
    processing_feedback_raven = State()


# --- NEW FSM STATES ---
class ShortTermMemoryStates(StatesGroup):
    showing_words_stm = State()
    waiting_for_recall_stm = State()


class BatteryCycleStates(StatesGroup):
    idle = State()  # Initial/completed state
    showing_main_instruction = State()  # Prompt to start control phase

    running_control_phase_test = State()  # Generic state indicating a test is running in control phase
    control_phase_inter_test_pause = State()

    awaiting_medication_acknowledgement = State()  # After control phase, prompt for medication
    medication_timer_active = State()  # 30-min timer for medication

    showing_post_medication_instruction = State()  # Prompt to start medication phase tests
    running_medication_phase_test = State()  # Generic state indicating a test is running in medication phase
    medication_phase_inter_test_pause = State()

    battery_completed_prompt_next_action = State()  # Battery finished, prompt for what's next (e.g., placebo if implemented)
    battery_stopped = State()  # State after /stopbattery
