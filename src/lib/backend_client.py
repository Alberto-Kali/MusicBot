import asyncio
from urllib.parse import quote

import aiohttp

from config import BACKEND_INTERNAL_URL, BACKEND_PUBLIC_URL

_CHUNK_SIZE = 64 * 1024


async def _get_json(path: str, params: dict | None = None) -> dict:
    url = f"{BACKEND_INTERNAL_URL.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as response:
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"backend {response.status}: {text}")
            return await response.json()


async def search_tracks(query: str, limit: int = 10) -> list[dict]:
    payload = await _get_json("/api/v1/search", params={"q": query, "limit": limit})
    return payload.get("items", [])


async def get_track_info_by_video_id(video_id: str) -> dict:
    return await _get_json(f"/api/v1/tracks/{video_id}")


def get_stream_url(video_id: str) -> str:
    return f"{BACKEND_PUBLIC_URL.rstrip('/')}/api/v1/stream/{quote(video_id)}.mp3"


async def download_track_bytes(video_id: str, progress_callback=None) -> bytes:
    url = f"{BACKEND_INTERNAL_URL.rstrip('/')}/api/v1/download/{quote(video_id)}.mp3"
    timeout = aiohttp.ClientTimeout(total=3600)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(f"backend {response.status}: {text}")

            total = int(response.headers.get("Content-Length", "0") or "0")
            downloaded = 0
            chunks = []
            last_bucket = -1

            async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                chunks.append(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    percent = min(int(downloaded * 100 / total), 99)
                    bucket = percent // 10
                    if bucket > last_bucket:
                        last_bucket = bucket
                        await progress_callback(percent)

            return b"".join(chunks)


async def download_thumbnail_bytes(url: str | None) -> bytes | None:
    if not url:
        return None

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                return None
            return await response.read()


async def warmup_backend() -> None:
    retries = 20
    for _ in range(retries):
        try:
            await _get_json("/health")
            return
        except Exception:
            await asyncio.sleep(1)
    raise RuntimeError("backend не отвечает по /health")
