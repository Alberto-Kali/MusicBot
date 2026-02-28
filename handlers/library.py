from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from lib.controldb import get_or_create_user, get_user_library, remove_from_library, get_track_by_video_id
from utils.keyboards import get_main_keyboard
import os
from config import TEMP_DIR
import aiohttp

router = Router()

async def show_library(message: Message):
    user = await get_or_create_user(message.from_user.id)
    tracks = await get_user_library(user.id)
    if not tracks:
        await message.answer("Ваша библиотека пуста.", reply_markup=get_main_keyboard())
        return

    builder = InlineKeyboardBuilder()
    for track in tracks:
        builder.button(text=f"{track.artist} - {track.title}", callback_data=f"lib_track:{track.id}")
    builder.button(text="🔙 Главное меню", callback_data="back_to_menu")
    builder.adjust(1)

    await message.answer("Ваша библиотека:", reply_markup=builder.as_markup())

@router.message(Command("library"))
async def cmd_library(message: Message):
    await show_library(message)

@router.callback_query(F.data.startswith("lib_track:"))
async def show_library_track(callback: CallbackQuery):
    track_id = int(callback.data.split(':')[1])
    # Получаем трек из БД
    from lib.controldb import AsyncSessionLocal
    from models import Track
    async with AsyncSessionLocal() as session:
        track = await session.get(Track, track_id)
        if not track:
            await callback.answer("Трек не найден")
            return

    # Скачиваем обложку временно
    thumb_path = None
    if track.thumbnail:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(track.thumbnail) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        thumb_path = os.path.join(TEMP_DIR, f"thumb_{track.video_id}.jpg")
                        with open(thumb_path, 'wb') as f:
                            f.write(img_data)
        except Exception as e:
            print(f"Error downloading thumbnail: {e}")

    builder = InlineKeyboardBuilder()
    builder.button(text="⬇ Скачать", callback_data=f"download:{track.video_id}")
    builder.button(text="❌ Удалить из библиотеки", callback_data=f"del_lib:{track.id}")
    builder.button(text="🔙 Назад", callback_data="back_to_library")
    builder.adjust(1)

    if thumb_path:
        photo = FSInputFile(thumb_path)
        await callback.message.answer_photo(
            photo,
            caption=f"{track.artist} — {track.title}",
            reply_markup=builder.as_markup()
        )
        os.remove(thumb_path)
    else:
        await callback.message.answer(
            f"{track.artist} — {track.title}",
            reply_markup=builder.as_markup()
        )

    await callback.message.delete()  # удаляем список

@router.callback_query(F.data.startswith("download:"))
async def download_from_library(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(':')[1]
    # Получаем информацию о треке из БД
    track = await get_track_by_video_id(video_id)
    if not track:
        await callback.answer("Трек не найден")
        return

    # Создаём словарь, совместимый с track_info
    track_info = {
        'videoId': video_id,
        'title': track.title,
        'artist': track.artist,
        'thumbnail': track.thumbnail
    }
    # Сохраняем в state, чтобы process_track_callback мог использовать
    await state.update_data(search_results={video_id: track_info})
    # Эмулируем нажатие на трек
    await process_track_callback(callback, state)

@router.callback_query(F.data.startswith("del_lib:"))
async def delete_from_library(callback: CallbackQuery):
    track_id = int(callback.data.split(':')[1])
    user = await get_or_create_user(callback.from_user.id)
    await remove_from_library(user.id, track_id)
    await callback.answer("Трек удалён из библиотеки")
    await callback.message.delete()
    # Показываем обновлённую библиотеку
    await show_library(callback.message)

@router.callback_query(F.data == "back_to_library")
async def back_to_library(callback: CallbackQuery):
    await callback.message.delete()
    await show_library(callback.message)