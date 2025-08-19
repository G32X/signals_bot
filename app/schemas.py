from pydantic import BaseModel
from datetime import datetime

class WatchlistOut(BaseModel):
    id: int
    symbol: str
    class Config:
        from_attributes = True

class SignalOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    direction: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    confidence: str
    reason: str
    rr: float
    created_at: datetime
    class Config:
        from_attributes = True

class PositionOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    status: str
    opened_at: datetime
    closed_at: datetime | None = None
    class Config:
        from_attributes = True

class PortfolioRow(BaseModel):
    symbol: str
    timeframe: str
    entry: float
    last: float | None
    change_pct: float | None
    rr: float | None
