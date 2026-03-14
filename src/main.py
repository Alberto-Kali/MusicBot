import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import BOT_TOKEN
from database import init_db
from handlers import download, library, menu, playlists, radio, search
from lib.backend_client import warmup_backend

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

dp.include_router(menu.router)
dp.include_router(search.router)
dp.include_router(download.router)
dp.include_router(library.router)
dp.include_router(playlists.router)
dp.include_router(radio.router)


async def run_bot():
    await init_db()
    await warmup_backend()
    await dp.start_polling(bot)


async def main():
    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
