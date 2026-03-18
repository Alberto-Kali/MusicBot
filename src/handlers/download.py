import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaAudio
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.common import send_success_sticker
from lib.backend_client import download_thumbnail_bytes, download_track_bytes, get_track_info_by_video_id
from lib.controldb import (
    add_to_library,
    ensure_track,
    get_or_create_user,
    get_track_by_video_id,
    get_user_library,
)
from utils.keyboards import main_menu_keyboard

router = Router()
logger = logging.getLogger(__name__)


async def _load_audio_and_thumb(video_id: str, thumbnail_url: str | None, progress_callback):
    audio_bytes = await download_track_bytes(video_id, progress_callback=progress_callback)
    thumb_bytes = await download_thumbnail_bytes(thumbnail_url)
    return audio_bytes, thumb_bytes


def _progress_updater(progress_msg):
    last_percent = -1

    async def update(percent: int):
        nonlocal last_percent
        if percent <= last_percent:
            return
        last_percent = percent
        try:
            await progress_msg.edit_text(f"Загрузка: {percent}%")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise

    return update


async def send_track_and_actions(callback: CallbackQuery, state: FSMContext, video_id: str):
    data = await state.get_data()
    results_dict = data.get("search_results", {})
    track_info = results_dict.get(video_id)

    if not track_info:
        await callback.message.edit_text("Ошибка: информация о треке не найдена.")
        return

    progress_msg = await callback.message.edit_text("Загрузка: 0%")

    update_progress = _progress_updater(progress_msg)

    try:
        audio_bytes, thumb_bytes = await _load_audio_and_thumb(
            video_id,
            track_info.get("thumbnail"),
            update_progress,
        )
    except Exception as exc:
        logger.exception("download_failed video_id=%s error=%s", video_id, exc)
        await progress_msg.edit_text(f"Ошибка при скачивании: {exc}")
        return

    await progress_msg.edit_text("Файл получен. Отправляю в Telegram...")

    audio_file = BufferedInputFile(audio_bytes, filename=f"{video_id}.mp3")
    thumb_file = BufferedInputFile(thumb_bytes, filename=f"{video_id}.jpg") if thumb_bytes else None

    await callback.message.answer_audio(
        audio=audio_file,
        title=track_info["title"],
        performer=track_info["artist"],
        thumbnail=thumb_file,
        caption=f"{track_info['artist']} — {track_info['title']}",
    )

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

    await progress_msg.edit_text("Загрузка: 100%\nЧто делаем дальше?", reply_markup=builder.as_markup())


async def send_track_to_private_chat(callback: CallbackQuery, video_id: str, use_media_audio: bool = False):
    track = await get_track_by_video_id(video_id)
    if track:
        track_info = {
            "videoId": video_id,
            "title": track.title,
            "artist": track.artist,
            "thumbnail": track.thumbnail,
            "duration": track.duration,
        }
    else:
        track_info = await get_track_info_by_video_id(video_id)

    progress_msg = await callback.bot.send_message(callback.from_user.id, "Загрузка: 0%")

    update_progress = _progress_updater(progress_msg)

    try:
        audio_bytes, thumb_bytes = await _load_audio_and_thumb(
            video_id,
            track_info.get("thumbnail"),
            update_progress,
        )
    except Exception as exc:
        logger.exception("inline_download_failed video_id=%s error=%s", video_id, exc)
        await progress_msg.edit_text(f"Ошибка при скачивании: {exc}")
        return

    await progress_msg.edit_text("Файл получен. Отправляю в Telegram...")

    audio_file = BufferedInputFile(audio_bytes, filename=f"{video_id}.mp3")
    thumb_file = BufferedInputFile(thumb_bytes, filename=f"{video_id}.jpg") if thumb_bytes else None

    if use_media_audio:
        media = InputMediaAudio(
            media=audio_file,
            thumbnail=thumb_file,
            title=track_info["title"],
            performer=track_info["artist"],
            caption=f"{track_info['artist']} — {track_info['title']}",
        )
        await callback.bot.send_media_group(
            callback.from_user.id,
            media=[media],
        )
    else:
        await callback.bot.send_audio(
            callback.from_user.id,
            audio=audio_file,
            title=track_info["title"],
            performer=track_info["artist"],
            thumbnail=thumb_file,
            caption=f"{track_info['artist']} — {track_info['title']}",
        )

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
    is_in_library = any(t.id == db_track.id for t in await get_user_library(user.id))

    builder = InlineKeyboardBuilder()
    if is_in_library:
        builder.button(text="✅ Уже в библиотеке", callback_data="lib:already")
    else:
        builder.button(text="➕ Добавить в библиотеку", callback_data=f"lib:add:{db_track.id}")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)

    await progress_msg.edit_text("Загрузка: 100%\nТрек отправлен в личку.", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("track:"))
async def process_track_callback(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(":", 1)[1]
    await callback.answer()
    await send_track_and_actions(callback, state, video_id)


@router.callback_query(F.data.startswith("inline:dl:"))
async def process_inline_download_callback(callback: CallbackQuery):
    video_id = callback.data.split(":", 2)[2]
    await callback.answer("Скачиваю и отправляю в личные сообщения...")
    try:
        await send_track_to_private_chat(callback, video_id)
    except Exception as exc:
        logger.exception("inline_send_private_failed video_id=%s error=%s", video_id, exc)
        await callback.bot.send_message(
            callback.from_user.id,
            "Не удалось отправить трек. Откройте чат с ботом /start и попробуйте снова.",
        )


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
