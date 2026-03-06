from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.download import send_track_and_actions
from lib.backend_client import search_tracks
from states import RadioStates
from utils.keyboards import main_menu_keyboard

router = Router()


def radio_query_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔥 Хиты", callback_data="radio:preset:хиты")
    builder.button(text="🎧 Электроника", callback_data="radio:preset:электроника")
    builder.button(text="🎸 Рок", callback_data="radio:preset:рок")
    builder.button(text="🎤 Русский рэп", callback_data="radio:preset:русский рэп")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def radio_page_keyboard(page: int, total_pages: int, tracks: list[dict]):
    start = page * 5
    end = min(start + 5, len(tracks))
    pack = tracks[start:end]

    builder = InlineKeyboardBuilder()
    for t in pack:
        if t.get("videoId"):
            builder.button(text=f"{t['artist']} - {t['title']}", callback_data=f"radio:play:{t['videoId']}")

    if page > 0:
        builder.button(text="⬅ Предыдущие 5", callback_data="radio:page:prev")
    if page < total_pages - 1:
        builder.button(text="Следующие 5 ➡", callback_data="radio:page:next")
    builder.button(text="🏠 Главное меню", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup(), pack


async def render_radio_page(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tracks = data.get("radio_tracks", [])
    query = data.get("radio_query", "")
    page = int(data.get("radio_page", 0))

    if not tracks:
        await callback.message.edit_text("Сессия радио истекла. Запустите заново.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return

    total_pages = (len(tracks) + 4) // 5
    page = max(0, min(page, total_pages - 1))
    await state.update_data(radio_page=page)

    markup, pack = radio_page_keyboard(page, total_pages, tracks)
    text = (
        f"📻 Радио: {query}\n"
        f"Пакет {page + 1}/{total_pages} | Треков в пакете: {len(pack)}\n"
        "Выберите трек:"
    )
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "menu:radio")
async def menu_radio(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RadioStates.waiting_for_query)
    await callback.message.edit_text(
        "Радио-режим: отправьте жанр/настроение текстом или выберите готовый вариант.",
        reply_markup=radio_query_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("radio:preset:"))
async def radio_preset(callback: CallbackQuery, state: FSMContext):
    query = callback.data.split(":", 2)[2]
    await state.clear()

    tracks = await search_tracks(query, limit=25)
    if not tracks:
        await callback.message.edit_text("Не удалось подобрать радио.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return

    await state.update_data(
        radio_query=query,
        radio_tracks=[t for t in tracks if t.get("videoId")],
        radio_page=0,
        search_results={t["videoId"]: t for t in tracks if t.get("videoId")},
    )
    await render_radio_page(callback, state)


@router.message(RadioStates.waiting_for_query)
async def radio_query_message(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите жанр или настроение.")
        return

    await state.clear()
    await message.answer("Подбираю радио...")
    tracks = await search_tracks(query, limit=25)
    if not tracks:
        await message.answer("Ничего не найдено для радио.", reply_markup=main_menu_keyboard())
        return

    usable_tracks = [t for t in tracks if t.get("videoId")]
    await state.update_data(
        radio_query=query,
        radio_tracks=usable_tracks,
        radio_page=0,
        search_results={t["videoId"]: t for t in usable_tracks},
    )

    total_pages = (len(usable_tracks) + 4) // 5
    markup, pack = radio_page_keyboard(0, total_pages, usable_tracks)
    await message.answer(
        f"📻 Радио: {query}\nПакет 1/{total_pages} | Треков в пакете: {len(pack)}\nВыберите трек:",
        reply_markup=markup,
    )


@router.callback_query(F.data == "radio:page:next")
async def radio_page_next(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(radio_page=int(data.get("radio_page", 0)) + 1)
    await render_radio_page(callback, state)


@router.callback_query(F.data == "radio:page:prev")
async def radio_page_prev(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(radio_page=max(0, int(data.get("radio_page", 0)) - 1))
    await render_radio_page(callback, state)


@router.callback_query(F.data.startswith("radio:play:"))
async def radio_play_track(callback: CallbackQuery, state: FSMContext):
    video_id = callback.data.split(":")[-1]
    await callback.answer()
    await send_track_and_actions(callback, state, video_id)
