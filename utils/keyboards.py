from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск")],
            [KeyboardButton(text="📚 Моя библиотека")],
            [KeyboardButton(text="ℹ Помощь")]
        ],
        resize_keyboard=True
    )