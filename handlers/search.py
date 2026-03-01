import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultAudio,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_USERNAME, INLINE_SERVER_DOMAIN
from lib.youtubectrl import ensure_inline_mp3, get_track_info_by_video_id, search_tracks
from states import SearchStates
from utils.keyboards import back_to_menu_keyboard

router = Router()
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@router.callback_query(F.data == "menu:search")
async def ask_search_query(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_query)
    await state.update_data(search_prompt_message_id=callback.message.message_id)
    await callback.message.edit_text(
        "Введите название трека или исполнителя одним сообщением.",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    if query.startswith("--"):
        query = query[2:].strip()
    if not query:
        await message.answer("Запрос пустой. Напишите, что искать.")
        return

    data = await state.get_data()
    prompt_message_id = data.get("search_prompt_message_id")
    waiting_msg = await message.answer("Ищу треки...")
    tracks = await search_tracks(query, limit=10)

    try:
        await message.delete()
    except Exception:
        pass
    try:
        await waiting_msg.delete()
    except Exception:
        pass

    if not tracks:
        if prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    text="Ничего не найдено.",
                    reply_markup=back_to_menu_keyboard(),
                )
            except Exception:
                await message.answer("Ничего не найдено.", reply_markup=back_to_menu_keyboard())
        else:
            await message.answer("Ничего не найдено.", reply_markup=back_to_menu_keyboard())
        await state.clear()
        return

    results_dict = {t["videoId"]: t for t in tracks if t.get("videoId")}
    await state.update_data(search_results=results_dict)
    await state.set_state(None)

    builder = InlineKeyboardBuilder()
    for t in tracks[:10]:
        if not t.get("videoId"):
            continue
        builder.button(
            text=f"{t['artist']} - {t['title']} | ID: {t['videoId']}",
            callback_data=f"track:{t['videoId']}",
        )
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    if prompt_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                text="Выберите трек:",
                reply_markup=builder.as_markup(),
            )
            return
        except Exception:
            pass
    await message.answer("Выберите трек:", reply_markup=builder.as_markup())


@router.inline_query()
async def inline_music_search(inline_query: InlineQuery):
    raw_query = (inline_query.query or "").strip()
    experimental_mode = raw_query.startswith("--")
    query = raw_query[2:].strip() if experimental_mode else raw_query

    if len(query) < 2:
        await inline_query.answer(
            [],
            cache_time=1,
            is_personal=True,
            switch_pm_text="Введите минимум 2 символа для поиска",
            switch_pm_parameter="search",
        )
        return

    if experimental_mode:
        video_id = query
        if not YOUTUBE_ID_RE.match(video_id):
            await inline_query.answer(
                [],
                cache_time=1,
                is_personal=True,
                switch_pm_text="Для --режима укажите точный youtube_id (11 символов). Пример: --86DSeMKT_fM",
                switch_pm_parameter="search",
            )
            return

        try:
            await ensure_inline_mp3(video_id)
            track = await get_track_info_by_video_id(video_id)
        except Exception:
            await inline_query.answer(
                [],
                cache_time=1,
                is_personal=True,
                switch_pm_text="Не удалось подготовить mp3 по этому ID. Проверьте ID и попробуйте снова.",
                switch_pm_parameter="search",
            )
            return

        artist = track.get("artist", "Unknown")
        title = track.get("title", "Без названия")
        audio_url = f"{INLINE_SERVER_DOMAIN.rstrip('/')}/{video_id}.mp3"
        result = InlineQueryResultAudio(
            id=f"au_{video_id}",
            audio_url=audio_url,
            title=title,
            performer=artist,
            audio_duration=track.get("duration"),
            caption=f"{artist} — {title} | ID: {video_id}",
        )
        await inline_query.answer([result], cache_time=1, is_personal=True)
        return

    tracks = await search_tracks(query, limit=10)

    results = []
    for track in tracks:
        video_id = track.get("videoId")
        if not video_id:
            continue
        artist = track.get("artist", "Unknown")
        title = track.get("title", "Без названия")
        link = f"https://music.youtube.com/watch?v={video_id}"
        footer = f"@{BOT_USERNAME}" if BOT_USERNAME else "@music_bot"
        text = f"🎵 {artist} — {title}\nID: {video_id}\n{link}\n\n{footer}"
        results.append(
            InlineQueryResultArticle(
                id=video_id,
                title=f"{artist} — {title}",
                description=f"ID: {video_id}",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⬇ Скачать в ЛС", callback_data=f"inline:dl:{video_id}")],
                        [InlineKeyboardButton(text="▶ Открыть в YouTube Music", url=link)],
                    ]
                ),
            )
        )

    await inline_query.answer(results[:10], cache_time=2, is_personal=True)
