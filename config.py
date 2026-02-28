import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "TK")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./music_bot.db")
TEMP_DIR = "./tmp"
