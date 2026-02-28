# lib/youtubectrl.py
import asyncio
import yt_dlp
from ytmusicapi import YTMusic
import os
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, error
from PIL import Image
import requests
from io import BytesIO
from config import TEMP_DIR

ytmusic = YTMusic('browser.json')  # или OAuth, если нужно

def search_tracks_sync(query: str, limit: int = 5) -> list[dict]:
    """Поиск треков, возвращает список с videoId, title, artist, duration, thumbnail"""
    results = ytmusic.search(query, filter='songs', limit=limit)
    tracks = []
    for r in results:
        tracks.append({
            'videoId': r['videoId'],
            'title': r['title'],
            'artist': r['artists'][0]['name'] if r.get('artists') else 'Unknown',
            'duration': r.get('duration_seconds'),
            'thumbnail': r['thumbnails'][-1]['url'] if r.get('thumbnails') else None
        })
    return tracks


async def search_tracks(query: str, limit: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search_tracks_sync, query, limit)


def download_audio(video_id: str, output_dir: str = './tmp') -> str:
    """Скачивает аудио в MP3 и возвращает путь к файлу"""
    url = f'https://www.youtube.com/watch?v={video_id}'
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{output_dir}/%(title)s.%(ext)s',
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # после конвертации расширение станет .mp3
        base, _ = os.path.splitext(filename)
        mp3_file = base + '.mp3'
        return mp3_file

def add_cover(mp3_path: str, cover_url: str):
    """Скачивает обложку и добавляет её в MP3-файл"""
    response = requests.get(cover_url)
    img = Image.open(BytesIO(response.content))
    # конвертируем в JPEG для ID3
    img_data = BytesIO()
    img.save(img_data, format='JPEG')
    img_data = img_data.getvalue()

    audio = EasyID3(mp3_path)
    audio['title'] = audio.get('title', [''])[0]
    audio['artist'] = audio.get('artist', [''])[0]
    audio.save()

    # Добавляем картинку через ID3
    audio_id3 = ID3(mp3_path)
    audio_id3.add(APIC(
        encoding=3,
        mime='image/jpeg',
        type=3,  # обложка
        desc='Cover',
        data=img_data
    ))
    audio_id3.save(v2_version=3)

async def download_and_prepare(video_id: str, cover_url: str = None) -> str:
    """Асинхронная обёртка для скачивания и добавления обложки"""
    loop = asyncio.get_event_loop()
    # скачиваем в потоке
    mp3_path = await loop.run_in_executor(None, download_audio, video_id)
    if cover_url:
        # добавляем обложку (тоже синхронно)
        await loop.run_in_executor(None, add_cover, mp3_path, cover_url)
    return mp3_path


async def download_audio_with_progress(video_id: str, cover_url: str, progress_callback):
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total:
                percent = d['downloaded_bytes'] / total * 100
                asyncio.run_coroutine_threadsafe(queue.put(percent), loop)
        elif d['status'] == 'finished':
            asyncio.run_coroutine_threadsafe(queue.put(100), loop)

    def download_task():
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{TEMP_DIR}/%(title)s.%(ext)s',
            'noplaylist': True,
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            mp3_file = base + '.mp3'

            thumb_path = None
            if cover_url:
                # Скачиваем обложку
                response = requests.get(cover_url)
                img = Image.open(BytesIO(response.content))
                thumb_path = os.path.join(TEMP_DIR, f"thumb_{video_id}.jpg")
                img.save(thumb_path, 'JPEG')
                # Встраивать обложку в mp3 не будем, используем thumb при отправке
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