from sqlalchemy import Column, Integer, String, Float, DateTime, Enum
from .db import Base
import enum

class SignalType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"

class PositionStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    signal_type = Column(Enum(SignalType))
    price = Column(Float)
    timestamp = Column(DateTime)

class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    buy_price = Column(Float)
    sell_price = Column(Float, nullable=True)
    status = Column(Enum(PositionStatus), default=PositionStatus.OPEN)
