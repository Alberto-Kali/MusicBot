import asyncio
from urllib.parse import quote
from urllib.parse import urlparse

import aiohttp
from aiohttp_socks import ProxyConnector

from config import BACKEND_INTERNAL_URL, BACKEND_PUBLIC_URL, CONTAINER_NO_PROXY, SOCKS5_PROXY

_CHUNK_SIZE = 64 * 1024
_NO_PROXY_HOSTS = tuple(
    host.strip().lower()
    for host in CONTAINER_NO_PROXY.split(",")
    if host.strip()
)


def _should_bypass_proxy(url: str) -> bool:
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return True
    for token in _NO_PROXY_HOSTS:
        token = token.lstrip(".")
        if host == token or host.endswith(f".{token}"):
            return True
    return False


def _make_session(url: str, timeout: aiohttp.ClientTimeout) -> aiohttp.ClientSession:
    if not SOCKS5_PROXY or _should_bypass_proxy(url):
        return aiohttp.ClientSession(timeout=timeout)
    connector = ProxyConnector.from_url(SOCKS5_PROXY)
    return aiohttp.ClientSession(timeout=timeout, connector=connector)


async def _get_json(path: str, params: dict | None = None) -> dict:
    url = f"{BACKEND_INTERNAL_URL.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=120)
    async with _make_session(url, timeout) as session:
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

    async with _make_session(url, timeout) as session:
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
    async with _make_session(url, timeout) as session:
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
