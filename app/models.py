from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum
from sqlalchemy.sql import func
from enum import Enum
from .db import Base

class SignalStatus(str, Enum):
    NEW = "NEW"

class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), unique=True, index=True, nullable=False)

class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), index=True, nullable=False)
    timeframe = Column(String(8), nullable=False)
    direction = Column(String(8), nullable=False)  # BUY / SELL
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    tp1 = Column(Float, nullable=False)
    tp2 = Column(Float, nullable=False)
    confidence = Column(String(32), default="medium")
    reason = Column(String(256), default="")
    rr = Column(Float, default=1.5)
    status = Column(SAEnum(SignalStatus), default=SignalStatus.NEW, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), index=True, nullable=False)
    timeframe = Column(String(8), nullable=False)
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    tp1 = Column(Float, nullable=False)
    tp2 = Column(Float, nullable=False)
    status = Column(SAEnum(PositionStatus), default=PositionStatus.OPEN, nullable=False)
    opened_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
