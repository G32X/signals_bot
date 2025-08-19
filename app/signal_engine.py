from typing import Optional, Dict, Any
import numpy as np
from .data import fetch_ohlcv

def _ema(values, length):
    if len(values) == 0:
        return np.array([])
    k = 2/(length+1)
    ema = np.zeros_like(values, dtype=float)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = (values[i]-ema[i-1]) * k + ema[i-1]
    return ema

def compute_entry(symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
    """
    Paprasta įėjimo logika:
    - BUY, kai EMA20 kerta EMA50 iš apačios į viršų.
    - SL ~5% žemiau įėjimo, TP1 ~+5%, TP2 ~+10%.
    """
    df = fetch_ohlcv(symbol, timeframe)
    if df.empty or len(df) < 60:
        return None
    closes = df["close"].to_numpy(float)
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    if len(ema20) < 2 or len(ema50) < 2:
        return None
    if not (ema20[-1] > ema50[-1] and ema20[-2] <= ema50[-2]):
        return None

    entry = float(closes[-1])
    stop = float(entry * 0.95)
    tp1 = float(entry * 1.05)
    tp2 = float(entry * 1.10)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "direction": "BUY",
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "confidence": "medium",
        "reason": "EMA20 crossed above EMA50",
        "rr": round((tp1-entry)/(entry-stop), 2) if entry > stop else 1.5,
    }

def compute_exit(symbol: str, timeframe: str, entry: float, stop: float, tp1: float, tp2: float) -> Optional[Dict[str, Any]]:
    """
    Išėjimo logika:
    - SELL, jei kaina < EMA50, arba ≤ SL, arba ≥ TP2.
    """
    df = fetch_ohlcv(symbol, timeframe)
    if df.empty:
        return None
    last = float(df["close"].iloc[-1])
    closes = df["close"].to_numpy(float)
    ema50 = _ema(closes, 50)
    if len(ema50) < 1:
        return None

    if last < float(ema50[-1]) or last <= stop or last >= tp2:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "direction": "SELL",
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "confidence": "medium",
            "reason": "Exit rule (EMA50/SL/TP2) met",
            "rr": 0.0,
            "is_exit": True,
        }
    return None
