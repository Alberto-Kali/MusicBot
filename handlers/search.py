from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from states import SearchStates
from lib.youtubectrl import search_tracks
from utils.keyboards import get_main_keyboard

router = Router()

@router.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query:
        await message.answer("Запрос не может быть пустым. Попробуйте снова.")
        return
    await message.answer("Ищу...")
    tracks = await search_tracks(query, limit=5)
    if not tracks:
        await message.answer("Ничего не найдено.", reply_markup=get_main_keyboard())
        await state.clear()
        return

    # Сохраняем результаты
    results_dict = {t['videoId']: t for t in tracks}
    await state.update_data(search_results=results_dict)

    builder = InlineKeyboardBuilder()
    for t in tracks:
        builder.button(text=f"{t['artist']} - {t['title']}", callback_data=f"track:{t['videoId']}")
    builder.button(text="🔙 Главное меню", callback_data="back_to_menu")
    builder.adjust(1)

    await message.answer("Найденные треки:", reply_markup=builder.as_markup())
    await state.set_state(None)  # выходим из состояния, дальше работаем через колбэки


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()