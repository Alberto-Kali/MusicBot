import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher

from config import BOT_TOKEN, INLINE_SERVER_HOST, INLINE_SERVER_PORT
from database import init_db
from handlers import download, library, menu, playlists, radio, search
from server import app as media_app
from server import ensure_media_dir

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

dp.include_router(menu.router)
dp.include_router(search.router)
dp.include_router(download.router)
dp.include_router(library.router)
dp.include_router(playlists.router)
dp.include_router(radio.router)


async def run_media_server():
    config = uvicorn.Config(
        media_app,
        host=INLINE_SERVER_HOST,
        port=INLINE_SERVER_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot():
    await init_db()
    await dp.start_polling(bot)


async def main():
    ensure_media_dir()
    await asyncio.gather(
        run_media_server(),
        run_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())
