from aiogram.types import CallbackQuery, Message

from config import STICKER_SUCCESS, STICKER_WELCOME
from utils.keyboards import main_menu_keyboard


async def safe_send_sticker(message: Message, sticker_id: str):
    if not sticker_id:
        return
    try:
        await message.answer_sticker(sticker_id)
    except Exception:
        # Стикеры опциональны, не ломаем основной сценарий.
        return


async def show_main_menu(message: Message, text: str = "Главное меню:"):
    await message.answer(text, reply_markup=main_menu_keyboard())


async def go_main_menu(callback: CallbackQuery, text: str = "Главное меню:"):
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


async def send_welcome_sticker(message: Message):
    await safe_send_sticker(message, STICKER_WELCOME)


async def send_success_sticker(message: Message):
    await safe_send_sticker(message, STICKER_SUCCESS)
