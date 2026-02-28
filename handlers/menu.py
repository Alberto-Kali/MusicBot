from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from lib.controldb import get_or_create_user
from states import SearchStates
from utils.keyboards import get_main_keyboard

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    await message.answer(
        "Добро пожаловать в музыкальный бот!\n"
        "Используйте кнопки ниже для навигации.",
        reply_markup=get_main_keyboard()
    )

@router.message(F.text == "🔍 Поиск")
async def search_button(message: Message, state: FSMContext):
    await message.answer(
        "Введите название трека или исполнителя:",
        reply_markup=ReplyKeyboardRemove()  # убираем главное меню, пока ждём ввод
    )
    await state.set_state(SearchStates.waiting_for_query)

@router.message(F.text == "📚 Моя библиотека")
async def library_button(message: Message, state: FSMContext):
    # очищаем состояние, если было
    await state.clear()
    # перенаправляем на команду библиотеки (можно вызвать обработчик напрямую или эмулировать)
    # лучше вызвать соответствующий обработчик
    from handlers.library import show_library  # импорт внутри функции чтобы избежать цикла
    await show_library(message)

@router.message(F.text == "ℹ Помощь")
async def help_button(message: Message):
    await message.answer(
        "Этот бот позволяет искать музыку на YouTube Music, скачивать треки и сохранять их в личную библиотеку.\n"
        "Используйте кнопки главного меню для навигации.",
        reply_markup=get_main_keyboard()
    )