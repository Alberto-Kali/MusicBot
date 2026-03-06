import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from backend.config import BACKEND_HOST, BACKEND_PORT, prepare_auth_files
from backend.music_service import MusicService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

music_service: MusicService | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global music_service
    auth = prepare_auth_files()
    music_service = MusicService(auth)
    yield


app = FastAPI(title="Music Backend", lifespan=lifespan)


def _service() -> MusicService:
    if music_service is None:
        raise RuntimeError("Music service is not initialized")
    return music_service


def _safe_remove(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.warning("Не удалось удалить временный файл: %s", path)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/v1/search")
async def search_tracks(q: str = Query(min_length=2), limit: int = Query(default=10, ge=1, le=25)):
    try:
        return {"items": await _service().search_tracks(q, limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка поиска: {exc}") from exc


@app.get("/api/v1/tracks/{video_id}")
async def get_track(video_id: str):
    try:
        return await _service().get_track_info(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка получения трека: {exc}") from exc


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
