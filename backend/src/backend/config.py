import base64
import os
from pathlib import Path

BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8080"))
BACKEND_TEMP_DIR = Path(os.getenv("BACKEND_TEMP_DIR", "./tmp/backend")).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://music:music@postgres:5432/music_bot").strip()

YTDLP_JS_RUNTIMES = os.getenv("YTDLP_JS_RUNTIMES", "deno:./include/deno")
YTDLP_REMOTE_COMPONENTS = os.getenv("YTDLP_REMOTE_COMPONENTS", "ejs:github")

BACKEND_COOKIES_FILE = os.getenv("BACKEND_COOKIES_FILE", "")
BACKEND_BROWSER_JSON_FILE = os.getenv("BACKEND_BROWSER_JSON_FILE", "")
BACKEND_COOKIES_B64 = os.getenv("BACKEND_COOKIES_B64", "")
BACKEND_BROWSER_JSON_B64 = os.getenv("BACKEND_BROWSER_JSON_B64", "")


class AuthFiles:
    def __init__(self, cookies_file: str | None, browser_json_file: str):
        self.cookies_file = cookies_file
        self.browser_json_file = browser_json_file


def _write_secret_from_b64(target: Path, raw_b64: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = base64.b64decode(raw_b64).decode("utf-8")
    target.write_text(payload, encoding="utf-8")
    target.chmod(0o600)
    return str(target)


def prepare_auth_files() -> AuthFiles:
    runtime_dir = BACKEND_TEMP_DIR / "auth"

    cookies_path: str | None = None
    if BACKEND_COOKIES_B64:
        cookies_path = _write_secret_from_b64(runtime_dir / "cookies.txt", BACKEND_COOKIES_B64)
    elif BACKEND_COOKIES_FILE and Path(BACKEND_COOKIES_FILE).exists():
        cookies_path = BACKEND_COOKIES_FILE

    if BACKEND_BROWSER_JSON_B64:
        browser_path = _write_secret_from_b64(runtime_dir / "browser.json", BACKEND_BROWSER_JSON_B64)
    elif BACKEND_BROWSER_JSON_FILE and Path(BACKEND_BROWSER_JSON_FILE).exists():
        browser_path = BACKEND_BROWSER_JSON_FILE
    else:
        raise RuntimeError(
            "Не задан browser.json: укажите BACKEND_BROWSER_JSON_B64 или BACKEND_BROWSER_JSON_FILE"
        )

    BACKEND_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return AuthFiles(cookies_file=cookies_path, browser_json_file=browser_path)
