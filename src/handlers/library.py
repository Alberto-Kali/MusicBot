from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from lib.controldb import get_or_create_user, get_track_by_id, get_user_library, remove_from_library
from utils.keyboards import main_menu_keyboard

router = Router()


def library_list_keyboard(tracks):
    builder = InlineKeyboardBuilder()
    for track in tracks[:50]:
        builder.button(
            text=f"{track.artist} - {track.title} | ID: {track.video_id}",
            callback_data=f"library:track:{track.id}",
        )
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


async def show_library(message: Message):
    user = await get_or_create_user(message.from_user.id)
    tracks = await get_user_library(user.id)
    if not tracks:
        await message.answer("Ваша библиотека пока пустая.", reply_markup=main_menu_keyboard())
        return

    await message.answer("Ваша библиотека:", reply_markup=library_list_keyboard(tracks))


@router.message(Command("library"))
async def cmd_library(message: Message):
    await show_library(message)


@router.callback_query(F.data == "menu:library")
async def menu_library(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id)
    tracks = await get_user_library(user.id)

    if not tracks:
        await callback.message.edit_text("Ваша библиотека пока пустая.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return

    await callback.message.edit_text("Ваша библиотека:", reply_markup=library_list_keyboard(tracks))
    await callback.answer()


@router.callback_query(F.data.startswith("library:track:"))
async def show_library_track(callback: CallbackQuery):
    track_id = int(callback.data.split(":")[-1])
    track = await get_track_by_id(track_id)
    if not track:
        await callback.answer("Трек не найден", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="⬇ Скачать", callback_data=f"lib:download:{track.video_id}")
    builder.button(text="❌ Удалить из библиотеки", callback_data=f"library:delete:{track.id}")
    builder.button(text="🔙 К библиотеке", callback_data="menu:library")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"{track.artist} — {track.title}\nID: {track.video_id}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("library:delete:"))
async def delete_track_from_library(callback: CallbackQuery):
    track_id = int(callback.data.split(":")[-1])
    user = await get_or_create_user(callback.from_user.id)
    await remove_from_library(user.id, track_id)
    await callback.answer("Трек удалён")

    tracks = await get_user_library(user.id)
    if not tracks:
        await callback.message.edit_text("Библиотека теперь пустая.", reply_markup=main_menu_keyboard())
        return

    await callback.message.edit_text("Ваша библиотека:", reply_markup=library_list_keyboard(tracks))
