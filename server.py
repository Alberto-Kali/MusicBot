import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from config import INLINE_MEDIA_DIR

app = FastAPI(title="Music Inline Media Server")


def _track_path(track_id: str) -> Path:
    safe_track_id = track_id.strip().replace("/", "").replace("..", "")
    return Path(INLINE_MEDIA_DIR) / f"{safe_track_id}.mp3"


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/{track_id}.mp3")
async def get_track(track_id: str):
    path = _track_path(track_id)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Track not found")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"{track_id}.mp3",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def ensure_media_dir() -> None:
    os.makedirs(INLINE_MEDIA_DIR, exist_ok=True)
