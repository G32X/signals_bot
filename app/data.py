import requests, pandas as pd, yfinance as yf

def get_price(ticker: str) -> float | None:
    try:
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            return float(data["Close"].iloc[-1])
    except Exception:
        return None
    return None

def get_ohlcv(ticker: str, interval="1d", limit=100):
    try:
        data = yf.Ticker(ticker).history(period="6mo", interval=interval)
        if data.empty:
            return []
        return [
            {
                "time": str(idx),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
            for idx, row in data.tail(limit).iterrows()
        ]
    except Exception:
        return []
