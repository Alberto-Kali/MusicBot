from sqlalchemy import Column, Integer, String, BigInteger, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String)
    first_name = Column(String)
    library = relationship('UserLibrary', back_populates='user', cascade='all, delete-orphan')

class Track(Base):
    __tablename__ = 'tracks'
    id = Column(Integer, primary_key=True)
    video_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    artist = Column(String)
    duration = Column(Integer)
    thumbnail = Column(String)
    library_entries = relationship('UserLibrary', back_populates='track')

class UserLibrary(Base):
    __tablename__ = 'user_library'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    track_id = Column(Integer, ForeignKey('tracks.id'), primary_key=True)
    added_at = Column(DateTime, default=func.now())
    user = relationship('User', back_populates='library')
    track = relationship('Track', back_populates='library_entries')