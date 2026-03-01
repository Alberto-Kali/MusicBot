from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select

from database import AsyncSessionLocal
from models import Playlist, PlaylistTrack, Track, User, UserLibrary


async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None) -> User:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=telegram_id, username=username, first_name=first_name)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def get_track_by_video_id(video_id: str) -> Track | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Track).where(Track.video_id == video_id))
        return result.scalar_one_or_none()


async def get_track_by_id(track_id: int) -> Track | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Track, track_id)


async def add_track(track_data: dict) -> Track:
    async with AsyncSessionLocal() as session:
        track = Track(**track_data)
        session.add(track)
        await session.commit()
        await session.refresh(track)
        return track


async def ensure_track(track_data: dict) -> Track:
    existing = await get_track_by_video_id(track_data["video_id"])
    if existing:
        return existing
    return await add_track(track_data)


async def add_to_library(user_id: int, track_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(UserLibrary).where(UserLibrary.user_id == user_id, UserLibrary.track_id == track_id)
        )
        if existing.scalar_one_or_none():
            return False

        session.add(UserLibrary(user_id=user_id, track_id=track_id))
        await session.commit()
        return True


async def remove_from_library(user_id: int, track_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(UserLibrary).where(
                UserLibrary.user_id == user_id,
                UserLibrary.track_id == track_id,
            )
        )
        await session.commit()


async def get_user_library(user_id: int) -> list[Track]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Track).join(UserLibrary).where(UserLibrary.user_id == user_id).order_by(UserLibrary.added_at.desc())
        )
        return result.scalars().all()


async def create_playlist(user_id: int, name: str) -> Playlist:
    async with AsyncSessionLocal() as session:
        playlist = Playlist(user_id=user_id, name=name, share_token=uuid4().hex)
        session.add(playlist)
        await session.commit()
        await session.refresh(playlist)
        return playlist


async def get_user_playlists(user_id: int) -> list[Playlist]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.created_at.desc())
        )
        return result.scalars().all()


async def get_playlist(playlist_id: int) -> Playlist | None:
    async with AsyncSessionLocal() as session:
        return await session.get(Playlist, playlist_id)


async def delete_playlist(user_id: int, playlist_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
        )
        playlist = result.scalar_one_or_none()
        if not playlist:
            return False

        await session.delete(playlist)
        await session.commit()
        return True


async def get_playlist_tracks(playlist_id: int) -> list[Track]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Track)
            .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.added_at.desc())
        )
        return result.scalars().all()


async def add_track_to_playlist(playlist_id: int, track_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PlaylistTrack).where(
                PlaylistTrack.playlist_id == playlist_id,
                PlaylistTrack.track_id == track_id,
            )
        )
        if result.scalar_one_or_none():
            return False

        session.add(PlaylistTrack(playlist_id=playlist_id, track_id=track_id))
        await session.commit()
        return True


async def remove_track_from_playlist(playlist_id: int, track_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(PlaylistTrack).where(
                PlaylistTrack.playlist_id == playlist_id,
                PlaylistTrack.track_id == track_id,
            )
        )
        await session.commit()


async def get_playlist_by_token(share_token: str) -> Playlist | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Playlist).where(Playlist.share_token == share_token))
        return result.scalar_one_or_none()
