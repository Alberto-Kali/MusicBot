import asyncio
import json
import logging
import os
from pathlib import Path

import yt_dlp
from ytmusicapi import YTMusic

from backend.config import AuthFiles, BACKEND_TEMP_DIR, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS

logger = logging.getLogger(__name__)


class YtdlpLogProxy:
    def debug(self, msg):
        if isinstance(msg, str) and msg.startswith("[debug]"):
            logger.debug("[YTDLP] %s", msg)

    def info(self, msg):
        logger.info("[YTDLP] %s", msg)

    def warning(self, msg):
        logger.warning("[YTDLP] %s", msg)

    def error(self, msg):
        logger.error("[YTDLP] %s", msg)


def _parse_js_runtimes(value: str | dict | None) -> dict[str, dict]:
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


class MusicService:
    def __init__(self, auth_files: AuthFiles):
        self._auth = auth_files
        self._ytmusic = YTMusic(auth_files.browser_json_file)
        self._ytdlp_logger = YtdlpLogProxy()

        try:
            self._parsed_js_runtimes = _parse_js_runtimes(YTDLP_JS_RUNTIMES)
        except Exception as exc:
            logger.warning("Некорректный YTDLP_JS_RUNTIMES=%r err=%s", YTDLP_JS_RUNTIMES, exc)
            self._parsed_js_runtimes = {"deno": {}}

        try:
            self._parsed_remote_components = _parse_remote_components(YTDLP_REMOTE_COMPONENTS)
        except Exception as exc:
            logger.warning("Некорректный YTDLP_REMOTE_COMPONENTS=%r err=%s", YTDLP_REMOTE_COMPONENTS, exc)
            self._parsed_remote_components = ["ejs:github"]

    def _common_ydl_opts(self) -> dict:
        opts = {
            "noplaylist": True,
            "logger": self._ytdlp_logger,
            "quiet": True,
            "no_warnings": False,
        }

        if self._auth.cookies_file and os.path.exists(self._auth.cookies_file):
            opts["cookiefile"] = self._auth.cookies_file

        if self._parsed_js_runtimes:
            opts["js_runtimes"] = self._parsed_js_runtimes
        if self._parsed_remote_components:
            opts["remote_components"] = self._parsed_remote_components
        return opts

    def search_tracks_sync(self, query: str, limit: int = 10) -> list[dict]:
        results = self._ytmusic.search(query, filter="songs", limit=limit)
        tracks = []
        for item in results:
            video_id = item.get("videoId")
            if not video_id:
                continue
            tracks.append(
                {
                    "videoId": video_id,
                    "title": item.get("title") or "Без названия",
                    "artist": item.get("artists", [{}])[0].get("name", "Unknown"),
                    "duration": item.get("duration_seconds"),
                    "thumbnail": (item.get("thumbnails") or [{}])[-1].get("url"),
                }
            )
        return tracks

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        return await asyncio.to_thread(self.search_tracks_sync, query, limit)

    def get_track_info_sync(self, video_id: str) -> dict:
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {**self._common_ydl_opts(), "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        return {
            "videoId": video_id,
            "title": info.get("title") or "Без названия",
            "artist": info.get("uploader") or info.get("channel") or "Unknown",
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
        }

    async def get_track_info(self, video_id: str) -> dict:
        return await asyncio.to_thread(self.get_track_info_sync, video_id)

    def get_best_audio_format(self, video_id: str) -> str:
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {**self._common_ydl_opts(), "extract_flat": False, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        audio_only = [
            f
            for f in formats
            if f.get("acodec") not in (None, "none") and f.get("vcodec") == "none"
        ]
        if audio_only:
            audio_only.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
            return audio_only[0]["format_id"]

        any_audio = [f for f in formats if f.get("acodec") not in (None, "none")]
        if any_audio:
            any_audio.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
            return any_audio[0]["format_id"]

        raise RuntimeError("У видео нет доступного аудиопотока")

    def get_stream_info_sync(self, video_id: str) -> dict:
        url = f"https://www.youtube.com/watch?v={video_id}"
        chosen_format = self.get_best_audio_format(video_id)
        ydl_opts = {
            **self._common_ydl_opts(),
            "format": chosen_format,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        stream_url = info.get("url")
        if not stream_url:
            req = info.get("requested_formats") or []
            if req:
                stream_url = req[0].get("url")

        if not stream_url:
            raise RuntimeError("Не удалось получить stream URL")

        return {
            "videoId": video_id,
            "title": info.get("title") or "Без названия",
            "artist": info.get("uploader") or info.get("channel") or "Unknown",
            "duration": info.get("duration"),
            "stream_url": stream_url,
            "format_id": chosen_format,
        }

    async def get_stream_info(self, video_id: str) -> dict:
        return await asyncio.to_thread(self.get_stream_info_sync, video_id)

    def _download_with_format(self, video_id: str, outtmpl: str, format_selector: str) -> str:
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            **self._common_ydl_opts(),
            "format": format_selector,
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            mp3_path = base + ".mp3"
        return mp3_path

    def download_audio_sync(self, video_id: str) -> str:
        BACKEND_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        format_id = self.get_best_audio_format(video_id)
        outtmpl = str(BACKEND_TEMP_DIR / f"{video_id}.%(ext)s")
        try:
            mp3_path = self._download_with_format(video_id, outtmpl, format_id)
        except yt_dlp.utils.DownloadError:
            fallback = "bestaudio[acodec!=none]/best[acodec!=none]"
            mp3_path = self._download_with_format(video_id, outtmpl, fallback)

        target = BACKEND_TEMP_DIR / f"{video_id}.mp3"
        if mp3_path != str(target) and Path(mp3_path).exists():
            os.replace(mp3_path, target)
        return str(target)

    async def download_audio(self, video_id: str) -> str:
        return await asyncio.to_thread(self.download_audio_sync, video_id)
