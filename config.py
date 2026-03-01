import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "TK")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./music_bot.db")
TEMP_DIR = "./tmp"

# Нужен для deep-link шаринга плейлистов: https://t.me/<BOT_USERNAME>?start=pl_<token>
BOT_USERNAME = os.getenv("BOT_USERNAME", "musikinetbot")

# Опциональные file_id стикеров; если пусто или недоступно, бот просто продолжит без стикера.
STICKER_WELCOME = os.getenv("STICKER_WELCOME", "")
STICKER_SUCCESS = os.getenv("STICKER_SUCCESS", "")
COOKIE_FILE = os.getenv("COOKIE_FILE", "./cookies.txt")

# yt-dlp-ejs: JS runtime + remote EJS component для обхода anti-bot/challenge
YTDLP_JS_RUNTIMES = os.getenv("YTDLP_JS_RUNTIMES", "deno:./include/deno")
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github")
