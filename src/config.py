import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN обязательна")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://music:music@postgres:5432/music_bot",
).strip()
if DATABASE_URL.startswith("sqlite"):
    raise RuntimeError("SQLite больше не поддерживается. Используйте PostgreSQL (asyncpg).")

BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8080")
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8080")
SOCKS5_PROXY = os.getenv("SOCKS5_PROXY", "").strip()
CONTAINER_NO_PROXY = os.getenv(
    "CONTAINER_NO_PROXY",
    os.getenv("NO_PROXY", "localhost,127.0.0.1,::1,postgres,backend,telegram-webapp,telegram-bot"),
).strip()

# Legacy-переменные оставлены для обратной совместимости модулей.
TEMP_DIR = os.getenv("TEMP_DIR", "./tmp")
COOKIE_FILE = os.getenv("COOKIE_FILE", "./cookies.txt")
YTDLP_JS_RUNTIMES = os.getenv("YTDLP_JS_RUNTIMES", "deno:./include/deno")
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github")
INLINE_MEDIA_DIR = os.getenv("INLINE_MEDIA_DIR", "./inline_media")
INLINE_SERVER_HOST = os.getenv("INLINE_SERVER_HOST", "0.0.0.0")
INLINE_SERVER_PORT = int(os.getenv("INLINE_SERVER_PORT", "8080"))
INLINE_SERVER_DOMAIN = os.getenv("INLINE_SERVER_DOMAIN", BACKEND_PUBLIC_URL)

# Нужен для deep-link шаринга плейлистов: https://t.me/<BOT_USERNAME>?start=pl_<token>
BOT_USERNAME = os.getenv("BOT_USERNAME", "musikinetbot")

# Опциональные file_id стикеров; если пусто или недоступно, бот просто продолжит без стикера.
STICKER_WELCOME = os.getenv("STICKER_WELCOME", "")
STICKER_SUCCESS = os.getenv("STICKER_SUCCESS", "")
