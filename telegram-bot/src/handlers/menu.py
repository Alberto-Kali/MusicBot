from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.common import go_main_menu, send_welcome_sticker
from lib.controldb import get_or_create_user
from utils.keyboards import main_menu_keyboard

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("pl_"):
        token = parts[1][3:]
        from handlers.playlists import show_shared_playlist

        await show_shared_playlist(message, token, user.id)
        return

    await send_welcome_sticker(message)
    await message.answer(
        "Привет! Я музыкальный бот.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await go_main_menu(callback)


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery):
    await callback.message.edit_text(
        "Что умею:\n"
        "• искать и отправлять треки\n"
        "• хранить личную библиотеку\n"
        "• создавать и делиться плейлистами\n"
        "• радио-режим пакетами по 5 треков",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()
