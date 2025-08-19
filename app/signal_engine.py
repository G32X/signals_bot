from .models import SignalType, PositionStatus, Signal, Position
from sqlalchemy.orm import Session
from datetime import datetime
from .data import get_price

def run_signal_engine(db: Session, tickers: list[str]):
    for ticker in tickers:
        price = get_price(ticker)
        if not price:
            continue
        last_signal = db.query(Signal).filter(Signal.ticker==ticker).order_by(Signal.timestamp.desc()).first()
        if not last_signal or last_signal.signal_type == SignalType.SELL:
            signal = Signal(ticker=ticker, signal_type=SignalType.BUY, price=price, timestamp=datetime.utcnow())
            db.add(signal)
            db.commit()
            pos = Position(ticker=ticker, buy_price=price, status=PositionStatus.OPEN)
            db.add(pos)
            db.commit()
        elif last_signal.signal_type == SignalType.BUY:
            pos = db.query(Position).filter(Position.ticker==ticker, Position.status==PositionStatus.OPEN).first()
            if pos and price > pos.buy_price * 1.05:
                signal = Signal(ticker=ticker, signal_type=SignalType.SELL, price=price, timestamp=datetime.utcnow())
                db.add(signal)
                db.commit()
                pos.sell_price = price
                pos.status = PositionStatus.CLOSED
                db.commit()
