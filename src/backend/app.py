import logging
import os
import hashlib
import hmac
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.background import BackgroundTask

from backend.config import BACKEND_HOST, BACKEND_PORT, DATABASE_URL, TELEGRAM_BOT_TOKEN, prepare_auth_files
from backend.music_service import MusicService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

music_service: MusicService | None = None
db_engine: AsyncEngine | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global music_service, db_engine
    auth = prepare_auth_files()
    music_service = MusicService(auth)
    db_engine = create_async_engine(DATABASE_URL, echo=False)
    yield
    if db_engine is not None:
        await db_engine.dispose()


app = FastAPI(title="Music Backend", lifespan=lifespan)


def _service() -> MusicService:
    if music_service is None:
        raise RuntimeError("Music service is not initialized")
    return music_service


def _db() -> AsyncEngine:
    if db_engine is None:
        raise RuntimeError("DB engine is not initialized")
    return db_engine


def _safe_remove(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.warning("Не удалось удалить временный файл: %s", path)


class TelegramAuthPayload(BaseModel):
    init_data: str


class AddLibraryPayload(TelegramAuthPayload):
    video_id: str
    title: str | None = None
    artist: str | None = None
    duration: int | None = None
    thumbnail: str | None = None


class TrackRefPayload(TelegramAuthPayload):
    video_id: str


def _parse_init_data(init_data: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(init_data, keep_blank_values=True))


def _is_telegram_hash_valid(data: dict[str, str], bot_token: str) -> bool:
    recv_hash = data.get("hash", "")
    if not recv_hash:
        return False
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted((k, v) for k, v in data.items() if k != "hash")
    )
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc_hash, recv_hash)


def _extract_telegram_user_id(init_data: str) -> int:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN is not configured")

    data = _parse_init_data(init_data)
    if not _is_telegram_hash_valid(data, TELEGRAM_BOT_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid Telegram initData hash")

    auth_date_raw = data.get("auth_date", "")
    if auth_date_raw.isdigit():
        auth_ts = int(auth_date_raw)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts - auth_ts > 86400:
            raise HTTPException(status_code=403, detail="Telegram initData is too old")

    raw_user = data.get("user")
    if not raw_user:
        raise HTTPException(status_code=400, detail="user is missing in initData")
    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid user payload in initData") from exc

    user_id = user.get("id")
    if not isinstance(user_id, int):
        raise HTTPException(status_code=400, detail="Invalid Telegram user id")
    return user_id


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/v1/search")
async def search_tracks(q: str = Query(min_length=2), limit: int = Query(default=10, ge=1, le=25)):
    try:
        return {"items": await _service().search_tracks(q, limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка поиска: {exc}") from exc


@app.get("/api/v1/charts")
async def get_charts(country: str = Query(default="EN", min_length=2, max_length=2), limit: int = Query(default=20, ge=1, le=50)):
    try:
        return {"items": await _service().get_charts(country.upper(), limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка получения чартов: {exc}") from exc


@app.post("/api/v1/auth/telegram")
async def auth_telegram(payload: TelegramAuthPayload):
    telegram_id = _extract_telegram_user_id(payload.init_data)
    return {"ok": True, "telegram_id": telegram_id}


@app.post("/api/v1/library/me")
async def get_my_library(payload: TelegramAuthPayload, limit: int = Query(default=100, ge=1, le=500)):
    telegram_id = _extract_telegram_user_id(payload.init_data)
    sql = text(
        """
        SELECT
            t.video_id AS "videoId",
            COALESCE(t.title, 'Без названия') AS title,
            COALESCE(t.artist, 'Unknown') AS artist,
            t.duration AS duration,
            t.thumbnail AS thumbnail
        FROM users u
        JOIN user_library ul ON ul.user_id = u.id
        JOIN tracks t ON t.id = ul.track_id
        WHERE u.telegram_id = :telegram_id
        ORDER BY ul.added_at DESC
        LIMIT :limit
        """
    )
    try:
        async with _db().connect() as conn:
            result = await conn.execute(sql, {"telegram_id": telegram_id, "limit": limit})
            items = [dict(row._mapping) for row in result]
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка чтения библиотеки: {exc}") from exc


@app.post("/api/v1/library/add")
async def add_to_my_library(payload: AddLibraryPayload):
    telegram_id = _extract_telegram_user_id(payload.init_data)
    video_id = payload.video_id.strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    title = (payload.title or "Без названия").strip() or "Без названия"
    artist = (payload.artist or "Unknown").strip() or "Unknown"

    try:
        async with _db().begin() as conn:
            user_id_result = await conn.execute(
                text(
                    """
                    INSERT INTO users (telegram_id)
                    VALUES (:telegram_id)
                    ON CONFLICT (telegram_id) DO UPDATE SET telegram_id = EXCLUDED.telegram_id
                    RETURNING id
                    """
                ),
                {"telegram_id": telegram_id},
            )
            user_id = user_id_result.scalar_one()

            track_id_result = await conn.execute(
                text(
                    """
                    INSERT INTO tracks (video_id, title, artist, duration, thumbnail)
                    VALUES (:video_id, :title, :artist, :duration, :thumbnail)
                    ON CONFLICT (video_id) DO UPDATE
                    SET
                        title = COALESCE(EXCLUDED.title, tracks.title),
                        artist = COALESCE(EXCLUDED.artist, tracks.artist),
                        duration = COALESCE(EXCLUDED.duration, tracks.duration),
                        thumbnail = COALESCE(EXCLUDED.thumbnail, tracks.thumbnail)
                    RETURNING id
                    """
                ),
                {
                    "video_id": video_id,
                    "title": title,
                    "artist": artist,
                    "duration": payload.duration,
                    "thumbnail": payload.thumbnail,
                },
            )
            track_id = track_id_result.scalar_one()

            add_result = await conn.execute(
                text(
                    """
                    INSERT INTO user_library (user_id, track_id)
                    VALUES (:user_id, :track_id)
                    ON CONFLICT (user_id, track_id) DO NOTHING
                    RETURNING 1
                    """
                ),
                {"user_id": user_id, "track_id": track_id},
            )
            added = add_result.scalar_one_or_none() is not None

        return {"ok": True, "added": added}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка добавления в библиотеку: {exc}") from exc


@app.post("/api/v1/library/remove")
async def remove_from_my_library(payload: TrackRefPayload):
    telegram_id = _extract_telegram_user_id(payload.init_data)
    video_id = payload.video_id.strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    try:
        async with _db().begin() as conn:
            delete_result = await conn.execute(
                text(
                    """
                    DELETE FROM user_library ul
                    USING users u, tracks t
                    WHERE
                        u.telegram_id = :telegram_id
                        AND t.video_id = :video_id
                        AND ul.user_id = u.id
                        AND ul.track_id = t.id
                    RETURNING 1
                    """
                ),
                {"telegram_id": telegram_id, "video_id": video_id},
            )
            removed = delete_result.scalar_one_or_none() is not None
        return {"ok": True, "removed": removed}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка удаления из библиотеки: {exc}") from exc


@app.post("/api/v1/library/contains")
async def contains_in_my_library(payload: TrackRefPayload):
    telegram_id = _extract_telegram_user_id(payload.init_data)
    video_id = payload.video_id.strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id is required")

    try:
        async with _db().connect() as conn:
            exists_result = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM users u
                    JOIN user_library ul ON ul.user_id = u.id
                    JOIN tracks t ON t.id = ul.track_id
                    WHERE u.telegram_id = :telegram_id AND t.video_id = :video_id
                    LIMIT 1
                    """
                ),
                {"telegram_id": telegram_id, "video_id": video_id},
            )
            in_library = exists_result.scalar_one_or_none() is not None
        return {"ok": True, "in_library": in_library}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка проверки библиотеки: {exc}") from exc


@app.get("/api/v1/tracks/{video_id}")
async def get_track(video_id: str):
    try:
        return await _service().get_track_info(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка получения трека: {exc}") from exc


@app.get("/api/v1/direct-stream/{video_id}")
async def get_direct_stream(video_id: str):
    try:
        stream_info = await _service().get_direct_stream_info(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка direct stream URL: {exc}") from exc

    return {
        "videoId": stream_info["videoId"],
        "stream_url": stream_info["stream_url"],
        "duration": stream_info.get("duration"),
        "mime_type": stream_info.get("mime_type"),
        "expires_in": stream_info.get("expires_in"),
        "source": stream_info.get("source", "direct"),
    }


@app.get("/api/v1/stream/{video_id}.mp3")
async def stream_track(video_id: str, request: Request):
    try:
        stream_info = await _service().get_stream_info(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка stream URL: {exc}") from exc

    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    try:
        upstream = requests.get(
            stream_info["stream_url"],
            headers=headers,
            stream=True,
            timeout=(10, 30),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка upstream stream: {exc}") from exc

    if upstream.status_code >= 400:
        upstream.close()
        raise HTTPException(status_code=502, detail=f"Upstream stream status={upstream.status_code}")

    pass_headers = {
        "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        "Cache-Control": "public, max-age=60",
    }
    if upstream.headers.get("Content-Range"):
        pass_headers["Content-Range"] = upstream.headers["Content-Range"]
    if upstream.headers.get("Content-Length"):
        pass_headers["Content-Length"] = upstream.headers["Content-Length"]

    return StreamingResponse(
        upstream.iter_content(chunk_size=64 * 1024),
        status_code=upstream.status_code,
        headers=pass_headers,
        media_type="audio/mpeg",
        background=BackgroundTask(upstream.close),
    )


@app.get("/api/v1/download/{video_id}.mp3")
async def download_track(video_id: str):
    try:
        file_path = await _service().download_audio(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка скачивания: {exc}") from exc

    if not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="Файл после скачивания не найден")

    return FileResponse(
        file_path,
        media_type="audio/mpeg",
        filename=f"{video_id}.mp3",
        background=BackgroundTask(_safe_remove, file_path),
    )


if __name__ == "__main__":
    uvicorn.run("backend.app:app", host=BACKEND_HOST, port=BACKEND_PORT, log_level="info")
