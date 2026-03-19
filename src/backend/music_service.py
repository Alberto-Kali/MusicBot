import asyncio
import hashlib
import http.cookiejar
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
import yt_dlp
from ytmusicapi import YTMusic

from backend.config import AuthFiles, BACKEND_TEMP_DIR, SOCKS5_PROXY, YTDLP_JS_RUNTIMES, YTDLP_REMOTE_COMPONENTS

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
    _DEFAULT_VISITOR_DATA = "CgtsZG1ySnZiQWtSbyiMjuGSBg%3D%3D"
    _INNERTUBE_ORIGIN = "https://music.youtube.com"
    _PLAYER_CLIENTS = (
        {
            "name": "WEB",
            "client_name": "WEB",
            "header_client_name": "1",
            "client_version": "2.2021111",
            "api_key": "AIzaSyC9XL3ZjWddXya6X74dJoCTL-WEYFDNX3",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.157 Safari/537.36",
            "use_login": True,
        },
        {
            "name": "WEB_REMIX",
            "client_name": "WEB_REMIX",
            "header_client_name": "67",
            "client_version": "1.20220606.03.00",
            "api_key": "AIzaSyC9XL3ZjWddXya6X74dJoCTL-WEYFDNX30",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.157 Safari/537.36",
            "use_login": True,
        },
        {
            "name": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
            "client_name": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
            "header_client_name": "85",
            "client_version": "2.0",
            "api_key": "AIzaSyDCU8hByM-4DrUqRUYnGn-3llEO78bcxq8",
            "user_agent": "Mozilla/5.0 (PlayStation 4 5.55) AppleWebKit/601.2 (KHTML, like Gecko)",
            "use_login": False,
            "third_party_embed": True,
        },
    )

    def __init__(self, auth_files: AuthFiles):
        self._auth = auth_files
        self._ytmusic = YTMusic(auth_files.browser_json_file)
        self._ytdlp_logger = YtdlpLogProxy()
        self._stream_cache: dict[str, tuple[float, dict]] = {}
        self._stream_cache_lock = threading.Lock()
        self._cookie_header = self._load_cookie_header(auth_files.cookies_file)
        self._cookie_map = self._load_cookie_map(auth_files.cookies_file)
        self._http = requests.Session()

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

    @staticmethod
    def _load_cookie_header(cookie_file: str | None) -> str | None:
        if not cookie_file or not os.path.exists(cookie_file):
            return None

        try:
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as exc:
            logger.warning("Не удалось загрузить cookies из %s: %s", cookie_file, exc)
            return None

        cookies = []
        for cookie in jar:
            if "youtube.com" not in cookie.domain and "google.com" not in cookie.domain:
                continue
            cookies.append(f"{cookie.name}={cookie.value}")
        return "; ".join(cookies) if cookies else None

    @staticmethod
    def _load_cookie_map(cookie_file: str | None) -> dict[str, str]:
        if not cookie_file or not os.path.exists(cookie_file):
            return {}

        try:
            jar = http.cookiejar.MozillaCookieJar(cookie_file)
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as exc:
            logger.warning("Не удалось разобрать cookies из %s: %s", cookie_file, exc)
            return {}

        cookies: dict[str, str] = {}
        for cookie in jar:
            if "youtube.com" not in cookie.domain and "google.com" not in cookie.domain:
                continue
            cookies[cookie.name] = cookie.value
        return cookies

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
        if SOCKS5_PROXY:
            opts["proxy"] = SOCKS5_PROXY
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

    @staticmethod
    def _extract_url_from_format(fmt: dict) -> str | None:
        url = fmt.get("url")
        if isinstance(url, str) and url:
            return url

        cipher = fmt.get("signatureCipher") or fmt.get("cipher")
        if not isinstance(cipher, str) or not cipher:
            return None

        try:
            params = dict(part.split("=", 1) for part in cipher.split("&") if "=" in part)
            encoded_url = params.get("url")
            if not encoded_url:
                return None
            from urllib.parse import parse_qs, unquote

            decoded_url = unquote(encoded_url)
            sig = params.get("sig") or params.get("lsig")
            sp = params.get("sp", "signature")
            if sig:
                query = parse_qs(decoded_url.split("?", 1)[1] if "?" in decoded_url else "")
                query[sp] = [unquote(sig)]
                base = decoded_url.split("?", 1)[0]
                return f"{base}?{urlencode(query, doseq=True)}"
            return decoded_url
        except Exception:
            return None

    @staticmethod
    def _audio_format_score(fmt: dict) -> tuple[int, int]:
        mime = str(fmt.get("mimeType") or "")
        bitrate = int(fmt.get("bitrate") or fmt.get("averageBitrate") or 0)
        mp4_bonus = 1 if mime.startswith("audio/mp4") else 0
        return mp4_bonus, bitrate

    def _pick_direct_audio_format(self, streaming_data: dict | None) -> dict | None:
        if not isinstance(streaming_data, dict):
            return None
        adaptive_formats = streaming_data.get("adaptiveFormats") or []
        candidates = []
        for fmt in adaptive_formats:
            if not isinstance(fmt, dict):
                continue
            mime = str(fmt.get("mimeType") or "")
            if not mime.startswith("audio/"):
                continue
            url = self._extract_url_from_format(fmt)
            if not url:
                continue
            candidates.append({**fmt, "url": url})
        if not candidates:
            return None
        return max(candidates, key=self._audio_format_score)

    def _build_player_headers(self, client: dict) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Format-Version": "1",
            "X-YouTube-Client-Name": client.get("header_client_name", client["client_name"]),
            "X-YouTube-Client-Version": client["client_version"],
            "x-origin": self._INNERTUBE_ORIGIN,
            "Origin": self._INNERTUBE_ORIGIN,
            "Referer": f"{self._INNERTUBE_ORIGIN}/",
            "User-Agent": client["user_agent"],
        }
        if client.get("use_login") and self._cookie_header:
            headers["Cookie"] = self._cookie_header
            sapisid = self._cookie_map.get("SAPISID") or self._cookie_map.get("__Secure-3PAPISID")
            if sapisid:
                current_time = int(time.time())
                payload = f"{current_time} {sapisid} {self._INNERTUBE_ORIGIN}"
                digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
                headers["Authorization"] = f"SAPISIDHASH {current_time}_{digest}"
        return headers

    def _build_player_body(self, video_id: str, playlist_id: str | None, client: dict) -> dict:
        body = {
            "context": {
                "client": {
                    "clientName": client["client_name"],
                    "clientVersion": client["client_version"],
                    "hl": "en",
                    "gl": "US",
                    "visitorData": self._DEFAULT_VISITOR_DATA,
                }
            },
            "videoId": video_id,
        }
        if client.get("os_version"):
            body["context"]["client"]["osVersion"] = client["os_version"]
        if playlist_id:
            body["playlistId"] = playlist_id
        if client.get("third_party_embed"):
            body["context"]["thirdParty"] = {"embedUrl": f"https://www.youtube.com/watch?v={video_id}"}
        return body

    def _fetch_player_response(self, video_id: str, playlist_id: str | None, client: dict) -> dict:
        response = self._http.post(
            "https://music.youtube.com/youtubei/v1/player",
            params={"key": client["api_key"], "prettyPrint": "false"},
            headers=self._build_player_headers(client),
            json=self._build_player_body(video_id, playlist_id, client),
            timeout=(5, 12),
        )
        response.raise_for_status()
        return response.json()

    def get_direct_stream_info_sync(self, video_id: str, playlist_id: str | None = None) -> dict:
        cached = self._get_cached_stream_info(video_id)
        if cached and cached.get("source") == "direct":
            return cached

        last_playability_reason: str | None = None
        for client in self._PLAYER_CLIENTS:
            try:
                player_response = self._fetch_player_response(video_id, playlist_id, client)
            except Exception as exc:
                logger.warning("Direct player request failed client=%s video_id=%s err=%s", client["name"], video_id, exc)
                continue

            playability = player_response.get("playabilityStatus") or {}
            status = playability.get("status")
            if status != "OK":
                last_playability_reason = playability.get("reason") or status
                continue

            best_format = self._pick_direct_audio_format(player_response.get("streamingData"))
            if not best_format:
                logger.warning("Direct player returned no usable audio formats client=%s video_id=%s", client["name"], video_id)
                continue

            streaming_data = player_response.get("streamingData") or {}
            expires_in = int(streaming_data.get("expiresInSeconds") or 0)
            ttl = max(30, expires_in - 60) if expires_in else self._STREAM_CACHE_TTL_SEC
            payload = {
                "videoId": video_id,
                "title": ((player_response.get("videoDetails") or {}).get("title")) or "Без названия",
                "artist": ((player_response.get("videoDetails") or {}).get("author")) or "Unknown",
                "duration": int(((player_response.get("videoDetails") or {}).get("lengthSeconds")) or 0) or None,
                "stream_url": best_format["url"],
                "format_id": str(best_format.get("itag") or ""),
                "mime_type": best_format.get("mimeType"),
                "expires_in": expires_in or None,
                "source": "direct",
            }
            expires_at = time.monotonic() + ttl
            with self._stream_cache_lock:
                self._stream_cache[video_id] = (expires_at, payload)
            return payload

        raise RuntimeError(last_playability_reason or "Не удалось получить direct stream URL")

    async def get_direct_stream_info(self, video_id: str, playlist_id: str | None = None) -> dict:
        return await asyncio.to_thread(self.get_direct_stream_info_sync, video_id, playlist_id)

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
