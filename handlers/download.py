import os
from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from lib.youtubectrl import download_audio_with_progress
from lib.controldb import get_track_by_video_id, add_track, get_or_create_user, add_to_library
from utils.keyboards import get_main_keyboard

router = Router()

@router.callback_query(F.data.startswith("track:"))
async def process_track_callback(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(':')[1]
    await callback.answer()

    data = await state.get_data()
    results_dict = data.get('search_results', {})
    track_info = results_dict.get(video_id)

    if not track_info:
        await callback.message.edit_text("Ошибка: информация о треке не найдена.")
        return

    progress_msg = await callback.message.edit_text("Загрузка: 0%")

    async def update_progress(percent: int):
        await progress_msg.edit_text(f"Загрузка: {percent}%")

    try:
        mp3_path, thumb_path = await download_audio_with_progress(
            video_id, track_info.get('thumbnail'), update_progress
        )
    except Exception as e:
        await progress_msg.edit_text(f"Ошибка при скачивании: {e}")
        return

    # Отправляем аудио
    audio_file = FSInputFile(mp3_path)
    thumb_file = FSInputFile(thumb_path) if thumb_path and os.path.exists(thumb_path) else None

    caption = f"*{track_info['artist']}* — {track_info['title']}"
    await callback.message.answer_audio(
        audio_file,
        title=track_info['title'],
        performer=track_info['artist'],
        thumb=thumb_file,
        caption=caption,
        parse_mode="Markdown"
    )

    # Удаляем временные файлы
    os.remove(mp3_path)
    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    # Сохраняем трек в БД, если его нет
    db_track = await get_track_by_video_id(video_id)
    if not db_track:
        db_track = await add_track({
            'video_id': video_id,
            'title': track_info['title'],
            'artist': track_info['artist'],
            'duration': track_info.get('duration'),
            'thumbnail': track_info.get('thumbnail')
        })

    # Проверяем, есть ли трек в библиотеке у пользователя
    user = await get_or_create_user(callback.from_user.id)
    # нужно проверить наличие в библиотеке (запрос к UserLibrary)
    # упростим: всегда показываем кнопку добавления (если нет, добавится, если есть — не даст добавить повторно)
    # Лучше сделать проверку и показывать соответствующую кнопку
    from lib.controldb import get_user_library  # избегаем циклического импорта
    library = await get_user_library(user.id)
    is_in_library = any(t.id == db_track.id for t in library)

    builder = InlineKeyboardBuilder()
    if is_in_library:
        builder.button(text="✅ В библиотеке", callback_data="already_in_lib")
    else:
        builder.button(text="➕ Добавить в библиотеку", callback_data=f"add:{db_track.id}")
    builder.button(text="🔙 Главное меню", callback_data="back_to_menu")
    builder.adjust(1)

    await callback.message.answer("Что дальше?", reply_markup=builder.as_markup())
    await callback.message.delete()  # удаляем сообщение с прогрессом

@router.callback_query(F.data == "already_in_lib")
async def already_in_lib(callback: CallbackQuery):
    await callback.answer("Этот трек уже в вашей библиотеке")