import asyncio
import json
import logging
import os
from io import BytesIO

import requests
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from PIL import Image
from ytmusicapi import YTMusic

from config import COOKIE_FILE, INLINE_MEDIA_DIR, TEMP_DIR, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS

logger = logging.getLogger(__name__)

ytmusic = YTMusic("browser.json")


class YtdlpLogProxy:
    """Проксируем внутренние сообщения yt-dlp в обычный logging."""

    def debug(self, msg):
        if isinstance(msg, str) and msg.startswith("[debug]"):
            logger.debug("[YTDLP] %s", msg)

    def info(self, msg):
        logger.info("[YTDLP] %s", msg)

    def warning(self, msg):
        logger.warning("[YTDLP] %s", msg)

    def error(self, msg):
        logger.error("[YTDLP] %s", msg)


_YTDLP_PROXY = YtdlpLogProxy()


def _parse_js_runtimes(value: str | dict | None) -> dict[str, dict]:
    """
    Поддержка форматов:
    - dict (уже готовый)
    - JSON-строка: {"deno":{"path":"./include/deno"}}
    - CLI-строка: deno:./include/deno,node:/usr/bin/node
    - CLI-строка без пути: deno
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return value

    text = str(value).strip()
    if not text:
        return {}

    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("YTDLP_JS_RUNTIMES JSON должен быть объектом")
        normalized: dict[str, dict] = {}
        for runtime, cfg in parsed.items():
            if cfg is None:
                normalized[str(runtime)] = {}
            elif isinstance(cfg, dict):
                normalized[str(runtime)] = cfg
            else:
                raise ValueError("YTDLP_JS_RUNTIMES: значение runtime должно быть объектом или null")
        return normalized

    runtimes: dict[str, dict] = {}
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        runtime, sep, path = token.partition(":")
        runtime = runtime.strip().lower()
        if not runtime:
            continue
        if sep and path.strip():
            runtimes[runtime] = {"path": path.strip()}
        else:
            runtimes[runtime] = {}
    return runtimes


def _parse_remote_components(value: str | list | tuple | set | None) -> list[str]:
    """
    Поддержка форматов:
    - list/tuple/set
    - JSON-строка: ["ejs:github"]
    - CLI-строка: ejs:github,ejs:npm
    """
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]

    text = str(value).strip()
    if not text:
        return []

    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("YTDLP_REMOTE_COMPONENTS JSON должен быть массивом")
        return [str(v).strip() for v in parsed if str(v).strip()]

    return [p.strip() for p in text.split(",") if p.strip()]


try:
    PARSED_JS_RUNTIMES = _parse_js_runtimes(YTDLP_JS_RUNTIMES)
except Exception as exc:
    logger.warning("[YTDLP] invalid YTDLP_JS_RUNTIMES=%r err=%s; fallback to {'deno': {}}", YTDLP_JS_RUNTIMES, exc)
    PARSED_JS_RUNTIMES = {"deno": {}}

try:
    PARSED_REMOTE_COMPONENTS = _parse_remote_components(YTDLP_REMOTE_COMPONENTS)
except Exception as exc:
    logger.warning(
        "[YTDLP] invalid YTDLP_REMOTE_COMPONENTS=%r err=%s; fallback to ['ejs:github']",
        YTDLP_REMOTE_COMPONENTS,
        exc,
    )
    PARSED_REMOTE_COMPONENTS = ["ejs:github"]


def _cookiefile() -> str | None:
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        return COOKIE_FILE
    return None


def _common_ydl_opts() -> dict:
    opts = {
        "noplaylist": True,
        "logger": _YTDLP_PROXY,
        "quiet": True,
        "no_warnings": False,
    }
    cookie = _cookiefile()
    if cookie:
        opts["cookiefile"] = cookie

    # Параметры поддерживаются в yt-dlp-ejs и соответствуют CLI:
    # --js-runtimes deno:./include/deno --remote-components ejs:github
    if PARSED_JS_RUNTIMES:
        opts["js_runtimes"] = PARSED_JS_RUNTIMES
    if PARSED_REMOTE_COMPONENTS:
        opts["remote_components"] = PARSED_REMOTE_COMPONENTS
    return opts


def get_best_audio_format(video_id: str) -> str:
    """Подбирает format_id с аудио. Если не найдено, выбрасывает понятную ошибку."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        **_common_ydl_opts(),
        "extract_flat": False,
        "skip_download": True,
    }

    logger.info("[YTDLP] inspect_start video_id=%s", video_id)
    logger.info(
        "[YTDLP] ejs_mode video_id=%s js_runtimes=%s remote_components=%s",
        video_id,
        PARSED_JS_RUNTIMES,
        PARSED_REMOTE_COMPONENTS,
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats", [])
    logger.info("[YTDLP] inspect_done video_id=%s formats=%d", video_id, len(formats))

    audio_only = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") == "none"
    ]
    if audio_only:
        audio_only.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
        fmt = audio_only[0]["format_id"]
        logger.info("[YTDLP] selected_audio_only video_id=%s format_id=%s", video_id, fmt)
        return fmt

    any_audio = [f for f in formats if f.get("acodec") not in (None, "none")]
    if any_audio:
        any_audio.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
        fmt = any_audio[0]["format_id"]
        logger.info("[YTDLP] selected_muxed video_id=%s format_id=%s", video_id, fmt)
        return fmt

    logger.warning("[YTDLP] no_audio_formats video_id=%s", video_id)
    raise RuntimeError(
        "У видео нет доступного аудиопотока (похоже, доступны только image/service форматы)."
    )


def _download_with_format(video_id: str, outtmpl: str, format_selector: str, progress_hooks=None) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        **_common_ydl_opts(),
        "format": format_selector,
        "outtmpl": outtmpl,
        "progress_hooks": progress_hooks or [],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    logger.info("[YTDLP] download_attempt video_id=%s format=%s", video_id, format_selector)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        mp3_path = base + ".mp3"

    logger.info("[YTDLP] download_success video_id=%s mp3=%s", video_id, mp3_path)
    return mp3_path


def search_tracks_sync(query: str, limit: int = 5) -> list[dict]:
    results = ytmusic.search(query, filter="songs", limit=limit)
    tracks = []
    for r in results:
        tracks.append(
            {
                "videoId": r["videoId"],
                "title": r["title"],
                "artist": r["artists"][0]["name"] if r.get("artists") else "Unknown",
                "duration": r.get("duration_seconds"),
                "thumbnail": r["thumbnails"][-1]["url"] if r.get("thumbnails") else None,
            }
        )
    return tracks


async def search_tracks(query: str, limit: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search_tracks_sync, query, limit)


def get_track_info_by_video_id_sync(video_id: str) -> dict:
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        **_common_ydl_opts(),
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return {
        "videoId": video_id,
        "title": info.get("title") or "Без названия",
        "artist": info.get("uploader") or info.get("channel") or "Unknown",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }


async def get_track_info_by_video_id(video_id: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_track_info_by_video_id_sync, video_id)


def ensure_inline_mp3_sync(video_id: str) -> str:
    """
    Гарантирует наличие файла ./inline_media/<video_id>.mp3 и возвращает его путь.
    """
    os.makedirs(INLINE_MEDIA_DIR, exist_ok=True)
    target_mp3 = os.path.join(INLINE_MEDIA_DIR, f"{video_id}.mp3")
    if os.path.exists(target_mp3) and os.path.getsize(target_mp3) > 0:
        logger.info("[YTDLP] inline_mp3_cached video_id=%s path=%s", video_id, target_mp3)
        return target_mp3

    format_id = get_best_audio_format(video_id)
    outtmpl = os.path.join(INLINE_MEDIA_DIR, f"{video_id}.%(ext)s")
    try:
        mp3_path = _download_with_format(video_id, outtmpl, format_id)
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("[YTDLP] inline_selected_format_failed video_id=%s format=%s err=%s", video_id, format_id, exc)
        fallback = "bestaudio[acodec!=none]/best[acodec!=none]"
        mp3_path = _download_with_format(video_id, outtmpl, fallback)

    # На случай неожиданных имён после постпроцессора принудительно нормализуем.
    if mp3_path != target_mp3 and os.path.exists(mp3_path):
        os.replace(mp3_path, target_mp3)

    if not os.path.exists(target_mp3):
        raise RuntimeError("Не удалось подготовить mp3 для inline-отправки")

    logger.info("[YTDLP] inline_mp3_ready video_id=%s path=%s", video_id, target_mp3)
    return target_mp3


async def ensure_inline_mp3(video_id: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ensure_inline_mp3_sync, video_id)


def download_audio(video_id: str, output_dir: str = "./tmp") -> str:
    format_id = get_best_audio_format(video_id)
    outtmpl = f"{output_dir}/%(title)s.%(ext)s"

    try:
        return _download_with_format(video_id, outtmpl, format_id)
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("[YTDLP] selected_format_failed video_id=%s format=%s err=%s", video_id, format_id, exc)
        fallback = "bestaudio[acodec!=none]/best[acodec!=none]"
        return _download_with_format(video_id, outtmpl, fallback)


def add_cover(mp3_path: str, cover_url: str):
    response = requests.get(cover_url, timeout=10)
    img = Image.open(BytesIO(response.content))

    img_data = BytesIO()
    img.save(img_data, format="JPEG")
    img_data = img_data.getvalue()

    audio = EasyID3(mp3_path)
    audio["title"] = audio.get("title", [""])[0]
    audio["artist"] = audio.get("artist", [""])[0]
    audio.save()

    audio_id3 = ID3(mp3_path)
    audio_id3.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=img_data,
        )
    )
    audio_id3.save(v2_version=3)


async def download_and_prepare(video_id: str, cover_url: str = None) -> str:
    loop = asyncio.get_event_loop()
    mp3_path = await loop.run_in_executor(None, download_audio, video_id)
    if cover_url:
        await loop.run_in_executor(None, add_cover, mp3_path, cover_url)
    return mp3_path


async def download_audio_with_progress(video_id: str, cover_url: str, progress_callback):
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()

    format_id = await loop.run_in_executor(None, get_best_audio_format, video_id)
    logger.info("[YTDLP] progress_mode video_id=%s chosen_format=%s", video_id, format_id)

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                percent = d.get("downloaded_bytes", 0) / total * 100
                asyncio.run_coroutine_threadsafe(queue.put(percent), loop)
        elif d.get("status") == "finished":
            asyncio.run_coroutine_threadsafe(queue.put(100), loop)

    def download_task():
        outtmpl = f"{TEMP_DIR}/%(title)s.%(ext)s"
        try:
            mp3_file = _download_with_format(video_id, outtmpl, format_id, progress_hooks=[progress_hook])
        except yt_dlp.utils.DownloadError as exc:
            logger.warning("[YTDLP] selected_format_failed video_id=%s format=%s err=%s", video_id, format_id, exc)
            fallback = "bestaudio[acodec!=none]/best[acodec!=none]"
            try:
                mp3_file = _download_with_format(video_id, outtmpl, fallback, progress_hooks=[progress_hook])
            except yt_dlp.utils.DownloadError as exc2:
                logger.error("[YTDLP] fallback_failed video_id=%s err=%s", video_id, exc2)
                raise RuntimeError(
                    "Не удалось скачать аудио: формат недоступен или видео ограничено."
                ) from exc2

        thumb_path = None
        if cover_url:
            try:
                response = requests.get(cover_url, timeout=10)
                response.raise_for_status()
                img = Image.open(BytesIO(response.content))
                thumb_path = os.path.join(TEMP_DIR, f"thumb_{video_id}.jpg")
                img.save(thumb_path, "JPEG")
            except Exception as exc:
                logger.warning("[YTDLP] thumb_failed video_id=%s err=%s", video_id, exc)

        return mp3_file, thumb_path

    future = loop.run_in_executor(None, download_task)

    last_percent = 0
    while not future.done():
        try:
            percent = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        percent_int = int(percent)
        if percent_int // 10 > last_percent // 10:
            last_percent = percent_int
            await progress_callback(percent_int)

    return await future
