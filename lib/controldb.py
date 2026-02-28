# lib/controldb.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, Track, UserLibrary
from database import AsyncSessionLocal

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

async def add_track(track_data: dict) -> Track:
    async with AsyncSessionLocal() as session:
        track = Track(**track_data)
        session.add(track)
        await session.commit()
        await session.refresh(track)
        return track

async def add_to_library(user_id: int, track_id: int):
    async with AsyncSessionLocal() as session:
        entry = UserLibrary(user_id=user_id, track_id=track_id)
        session.add(entry)
        await session.commit()

async def remove_from_library(user_id: int, track_id: int):
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(UserLibrary).where(
                UserLibrary.user_id == user_id,
                UserLibrary.track_id == track_id
            )
        )
        await session.commit()

async def get_user_library(user_id: int) -> list[Track]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Track).join(UserLibrary).where(UserLibrary.user_id == user_id)
        )
        return result.scalars().all()