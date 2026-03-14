from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from lib.controldb import get_or_create_user

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )
    await message.answer(
        "Привет! Я музыкальный бот.\n"
        "Используй /search <запрос> для поиска треков.\n"
        "/library - твоя библиотека."
    )