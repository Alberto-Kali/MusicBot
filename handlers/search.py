from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from lib.youtubectrl import search_tracks
from states import SearchStates
from utils.keyboards import back_to_menu_keyboard

router = Router()


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
        builder.button(text=f"{t['artist']} - {t['title']}", callback_data=f"track:{t['videoId']}")
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

    tracks = await search_tracks(query, limit=10)
    results = []
    for track in tracks:
        video_id = track.get("videoId")
        if not video_id:
            continue
        artist = track.get("artist", "Unknown")
        title = track.get("title", "Без названия")
        link = f"https://music.youtube.com/watch?v={video_id}"
        text = f"🎵 {artist} — {title}\n{link}"
        download_callback = f"inline:dlm:{video_id}" if experimental_mode else f"inline:dl:{video_id}"
        download_text = "⬇ Скачать в ЛС (эксп.)" if experimental_mode else "⬇ Скачать в ЛС"
        results.append(
            InlineQueryResultArticle(
                id=video_id,
                title=f"{artist} — {title}",
                description="YouTube Music",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=download_text, callback_data=download_callback)],
                        [InlineKeyboardButton(text="▶ Открыть в YouTube Music", url=link)],
                    ]
                ),
            )
        )

    await inline_query.answer(results[:10], cache_time=2, is_personal=True)
