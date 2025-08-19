from pydantic import BaseModel
from datetime import datetime
from .models import SignalType, PositionStatus

class SignalSchema(BaseModel):
    id: int
    ticker: str
    signal_type: SignalType
    price: float
    timestamp: datetime

    class Config:
        from_attributes = True

class PositionSchema(BaseModel):
    id: int
    ticker: str
    buy_price: float
    sell_price: float | None
    status: PositionStatus

    class Config:
        from_attributes = True
