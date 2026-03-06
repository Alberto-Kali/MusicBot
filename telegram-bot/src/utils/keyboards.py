from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск", callback_data="menu:search")
    builder.button(text="📚 Библиотека", callback_data="menu:library")
    builder.button(text="🧩 Плейлисты", callback_data="menu:playlists")
    builder.button(text="📻 Радио", callback_data="menu:radio")
    builder.button(text="ℹ Помощь", callback_data="menu:help")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    return builder.as_markup()
