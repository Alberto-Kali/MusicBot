from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from lib.youtubectrl import search_tracks
from states import SearchStates
from utils.keyboards import back_to_menu_keyboard

router = Router()


@router.callback_query(F.data == "menu:search")
async def ask_search_query(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_query)
    await callback.message.edit_text(
        "Введите название трека или исполнителя одним сообщением.",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@router.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Запрос пустой. Напишите, что искать.")
        return

    await message.answer("Ищу треки...")
    tracks = await search_tracks(query, limit=10)
    if not tracks:
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

    await message.answer("Выберите трек:", reply_markup=builder.as_markup())
