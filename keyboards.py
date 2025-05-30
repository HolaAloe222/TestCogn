# keyboards.py
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

IKB = InlineKeyboardButton

ACTION_SELECTION_KEYBOARD_NEW = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            IKB(
                text="Пройти батарею тестов",
                callback_data="run_test_battery",
            )
        ],
        [
            IKB(
                text="Выбрать отдельный тест",
                callback_data="select_specific_test",
            )
        ],
    ]
)

ACTION_SELECTION_KEYBOARD_RETURNING = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            IKB(
                text="Пройти батарею тестов заново",
                callback_data="run_test_battery",
            )
        ],
        [
            IKB(
                text="Выбрать отдельный тест заново",
                callback_data="select_specific_test",
            )
        ],
        [
            IKB(
                text="Выйти (сбросить профиль)",
                callback_data="logout_profile",
            )
        ],
    ]
)
