import os
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.common import send_success_sticker
from lib.controldb import (
    add_to_library,
    ensure_track,
    get_or_create_user,
    get_track_by_video_id,
    get_user_library,
)
from lib.youtubectrl import download_audio_with_progress
from utils.keyboards import main_menu_keyboard

router = Router()
logger = logging.getLogger(__name__)


async def send_track_and_actions(callback: CallbackQuery, state: FSMContext, video_id: str):
    data = await state.get_data()
    results_dict = data.get("search_results", {})
    track_info = results_dict.get(video_id)

    if not track_info:
        await callback.message.edit_text("Ошибка: информация о треке не найдена.")
        return

    progress_msg = await callback.message.edit_text("Загрузка: 0%")

    async def update_progress(percent: int):
        await progress_msg.edit_text(f"Загрузка: {percent}%")

    try:
        mp3_path, thumb_path = await download_audio_with_progress(
            video_id,
            track_info.get("thumbnail"),
            update_progress,
        )
    except Exception as exc:
        logger.exception("download_failed video_id=%s error=%s", video_id, exc)
        await progress_msg.edit_text(f"Ошибка при скачивании: {exc}")
        return

    audio_file = FSInputFile(mp3_path)
    thumb_file = FSInputFile(thumb_path) if thumb_path and os.path.exists(thumb_path) else None

    await callback.message.answer_audio(
        audio_file,
        title=track_info["title"],
        performer=track_info["artist"],
        thumb=thumb_file,
        caption=f"{track_info['artist']} — {track_info['title']}",
    )

    os.remove(mp3_path)
    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    db_track = await ensure_track(
        {
            "video_id": video_id,
            "title": track_info["title"],
            "artist": track_info["artist"],
            "duration": track_info.get("duration"),
            "thumbnail": track_info.get("thumbnail"),
        }
    )

    user = await get_or_create_user(callback.from_user.id)
    library = await get_user_library(user.id)
    is_in_library = any(t.id == db_track.id for t in library)

    builder = InlineKeyboardBuilder()
    if is_in_library:
        builder.button(text="✅ Уже в библиотеке", callback_data="lib:already")
    else:
        builder.button(text="➕ Добавить в библиотеку", callback_data=f"lib:add:{db_track.id}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await progress_msg.edit_text("Что делаем дальше?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("track:"))
async def process_track_callback(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(":", 1)[1]
    await callback.answer()
    await send_track_and_actions(callback, state, video_id)


@router.callback_query(F.data == "lib:already")
async def already_in_lib(callback: CallbackQuery):
    await callback.answer("Трек уже есть в библиотеке")


@router.callback_query(F.data.startswith("lib:add:"))
async def add_track_to_user_library(callback: CallbackQuery):
    track_id = int(callback.data.split(":")[-1])
    user = await get_or_create_user(callback.from_user.id)
    added = await add_to_library(user.id, track_id)

    if added:
        await send_success_sticker(callback.message)
        await callback.message.edit_text(
            "Трек добавлен в библиотеку.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await callback.message.edit_text(
            "Этот трек уже в вашей библиотеке.",
            reply_markup=main_menu_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("lib:download:"))
async def download_from_library(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(":", 2)[2]
    track = await get_track_by_video_id(video_id)
    if not track:
        await callback.answer("Трек не найден", show_alert=True)
        return

    track_info = {
        "videoId": video_id,
        "title": track.title,
        "artist": track.artist,
        "thumbnail": track.thumbnail,
        "duration": track.duration,
    }
    await state.update_data(search_results={video_id: track_info})
    await send_track_and_actions(callback, state, video_id)
