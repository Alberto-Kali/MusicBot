from sqlalchemy import Column, Integer, String, BigInteger, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String)
    first_name = Column(String)

    library = relationship("UserLibrary", back_populates="user", cascade="all, delete-orphan")
    playlists = relationship("Playlist", back_populates="owner", cascade="all, delete-orphan")


class Track(Base):
    __tablename__ = "tracks"

    id = Column(Integer, primary_key=True)
    video_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    artist = Column(String)
    duration = Column(Integer)
    thumbnail = Column(String)

    library_entries = relationship("UserLibrary", back_populates="track")
    playlist_entries = relationship("PlaylistTrack", back_populates="track", cascade="all, delete-orphan")


class UserLibrary(Base):
    __tablename__ = "user_library"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), primary_key=True)
    added_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="library")
    track = relationship("Track", back_populates="library_entries")


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    share_token = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=func.now())

    owner = relationship("User", back_populates="playlists")
    tracks = relationship("PlaylistTrack", back_populates="playlist", cascade="all, delete-orphan")


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"

    playlist_id = Column(Integer, ForeignKey("playlists.id"), primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), primary_key=True)
    added_at = Column(DateTime, default=func.now())

    playlist = relationship("Playlist", back_populates="tracks")
    track = relationship("Track", back_populates="playlist_entries")
