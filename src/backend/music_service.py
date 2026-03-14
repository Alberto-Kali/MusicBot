import asyncio
import json
import logging
import os
import threading
import time
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
    _STREAM_CACHE_TTL_SEC = 600

    def __init__(self, auth_files: AuthFiles):
        self._auth = auth_files
        self._ytmusic = YTMusic(auth_files.browser_json_file)
        self._ytdlp_logger = YtdlpLogProxy()
        self._stream_cache: dict[str, tuple[float, dict]] = {}
        self._stream_cache_lock = threading.Lock()

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

    @staticmethod
    def _chart_items(bucket) -> list[dict]:
        if isinstance(bucket, dict):
            items = bucket.get("items", [])
            return items if isinstance(items, list) else []
        if isinstance(bucket, list):
            return bucket
        return []

    @staticmethod
    def _parse_duration_seconds(raw) -> int | None:
        if isinstance(raw, int):
            return raw
        if not isinstance(raw, str) or ":" not in raw:
            return None
        parts = raw.split(":")
        if len(parts) == 2:
            m, s = parts
            if m.isdigit() and s.isdigit():
                return int(m) * 60 + int(s)
        return None

    @staticmethod
    def _normalize_chart_country(country: str) -> str:
        code = (country or "").strip().upper()
        if not code:
            return "US"
        if code == "EN":
            return "US"
        return code

    def _fetch_charts_sections(self, country: str) -> list[dict]:
        charts = self._ytmusic.get_charts(country=country)
        if not isinstance(charts, dict):
            return []
        merged_items: list[dict] = []
        for section in ("songs", "videos", "trending"):
            merged_items.extend(self._chart_items(charts.get(section)))
        if not merged_items:
            merged_items.extend(self._chart_items(charts.get("items")))
        return merged_items

    def _charts_search_fallback(self, country: str, limit: int) -> list[dict]:
        # Резервный сценарий, если get_charts пустой/недоступен.
        queries = [
            f"top songs {country}",
            f"{country} hits",
            "billboard hot 100",
            "global top songs",
        ]
        seen: set[str] = set()
        tracks: list[dict] = []
        for q in queries:
            try:
                found = self.search_tracks_sync(q, limit=limit)
            except Exception as exc:
                logger.warning("Fallback charts search failed for query=%r err=%s", q, exc)
                continue
            for item in found:
                video_id = item.get("videoId")
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                tracks.append(item)
                if len(tracks) >= limit:
                    return tracks
        return tracks

    def get_charts_sync(self, country: str = "RU", limit: int = 20) -> list[dict]:
        normalized_country = self._normalize_chart_country(country)
        merged_items: list[dict] = []
        for candidate in (normalized_country, "US", "RU"):
            try:
                merged_items = self._fetch_charts_sections(candidate)
                if merged_items:
                    break
            except Exception as exc:
                logger.warning("Не удалось загрузить charts для %s: %s", candidate, exc)
                continue

        tracks: list[dict] = []
        seen: set[str] = set()
        for item in merged_items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)

            artist = "Unknown"
            artists = item.get("artists")
            if isinstance(artists, list) and artists:
                artist = artists[0].get("name") or artist

            duration = item.get("duration_seconds")
            if duration is None:
                duration = self._parse_duration_seconds(item.get("duration"))

            tracks.append(
                {
                    "videoId": video_id,
                    "title": item.get("title") or "Без названия",
                    "artist": artist,
                    "duration": duration,
                    "thumbnail": (item.get("thumbnails") or [{}])[-1].get("url"),
                }
            )
            if len(tracks) >= limit:
                break

        if tracks:
            return tracks

        fallback_tracks = self._charts_search_fallback(normalized_country, limit=limit)
        if fallback_tracks:
            logger.info("Charts fallback activated country=%s items=%d", normalized_country, len(fallback_tracks))
        else:
            logger.warning("Charts fallback returned empty country=%s", normalized_country)
        return fallback_tracks

    async def get_charts(self, country: str = "RU", limit: int = 20) -> list[dict]:
        return await asyncio.to_thread(self.get_charts_sync, country, limit)

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

    @staticmethod
    def _pick_best_audio_format_from_info(info: dict) -> dict | None:
        formats = info.get("formats", [])
        audio_only = [
            f
            for f in formats
            if f.get("acodec") not in (None, "none") and f.get("vcodec") == "none"
        ]
        if audio_only:
            audio_only.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
            return audio_only[0]

        any_audio = [f for f in formats if f.get("acodec") not in (None, "none")]
        if any_audio:
            any_audio.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
            return any_audio[0]
        return None

    def _get_cached_stream_info(self, video_id: str) -> dict | None:
        now = time.monotonic()
        with self._stream_cache_lock:
            entry = self._stream_cache.get(video_id)
            if not entry:
                return None
            expires_at, payload = entry
            if expires_at <= now:
                self._stream_cache.pop(video_id, None)
                return None
            return payload

    def _set_cached_stream_info(self, video_id: str, payload: dict) -> None:
        expires_at = time.monotonic() + self._STREAM_CACHE_TTL_SEC
        with self._stream_cache_lock:
            self._stream_cache[video_id] = (expires_at, payload)

    def get_stream_info_sync(self, video_id: str) -> dict:
        cached = self._get_cached_stream_info(video_id)
        if cached:
            return cached

        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            **self._common_ydl_opts(),
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        best_format = self._pick_best_audio_format_from_info(info)
        if not best_format:
            raise RuntimeError("У видео нет доступного аудиопотока")

        chosen_format = str(best_format.get("format_id") or "")
        stream_url = best_format.get("url")
        if not stream_url and chosen_format:
            # Fallback: запрашиваем explicit формат, если URL не пришёл в formats.
            ydl_opts_with_format = {
                **self._common_ydl_opts(),
                "format": chosen_format,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts_with_format) as ydl:
                fallback_info = ydl.extract_info(url, download=False)
            stream_url = fallback_info.get("url")
            if not stream_url:
                req = fallback_info.get("requested_formats") or []
                if req:
                    stream_url = req[0].get("url")

        if not stream_url:
            raise RuntimeError("Не удалось получить stream URL")

        payload = {
            "videoId": video_id,
            "title": info.get("title") or "Без названия",
            "artist": info.get("uploader") or info.get("channel") or "Unknown",
            "duration": info.get("duration"),
            "stream_url": stream_url,
            "format_id": chosen_format,
        }
        self._set_cached_stream_info(video_id, payload)
        return payload

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
