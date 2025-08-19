from typing import Optional
import os, time
import pandas as pd
import requests
from datetime import datetime, timezone
from .config import settings

_YF_ENABLE = os.getenv("YF_ENABLE_FALLBACK", "0").strip() in ("1", "true", "True", "yes")

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Connection": "keep-alive",
})

TD_BASE = "https://api.twelvedata.com/time_series"
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

_td_minute_window_start = 0.0
_td_minute_count = 0
_td_day_window_start = 0.0
_td_day_count = 0

def _now() -> float:
    return time.time()

def _td_allow() -> bool:
    global _td_minute_window_start, _td_minute_count, _td_day_window_start, _td_day_count
    t = _now()
    if t - _td_minute_window_start >= 60:
        _td_minute_window_start = t
        _td_minute_count = 0
    if t - _td_day_window_start >= 86400:
        _td_day_window_start = t
        _td_day_count = 0
    if _td_minute_count < settings.TD_MAX_PER_MINUTE and _td_day_count < settings.TD_MAX_PER_DAY:
        _td_minute_count += 1
        _td_day_count += 1
        return True
    return False

def _td_interval(tf: str) -> str:
    return {"1h": "1h", "1d": "1day"}[tf]

def _download_td(symbol: str, timeframe: str, outputsize: int) -> pd.DataFrame:
    if not TD_KEY or not _td_allow():
        return pd.DataFrame()
    candidates = [symbol, f"NASDAQ:{symbol}", f"NYSE:{symbol}", f"AMEX:{symbol}"]
    for sym in candidates:
        params = {
            "symbol": sym,
            "interval": _td_interval(timeframe),
            "apikey": TD_KEY,
            "format": "JSON",
            "outputsize": outputsize,
            "order": "ASC",
        }
        try:
            r = _session.get(TD_BASE, params=params, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            if "values" not in data or not data["values"]:
                continue
            df = pd.DataFrame(data["values"])
            for col in ("open","high","low","close","volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            tcol = "datetime" if "datetime" in df.columns else "time"
            df = df.rename(columns={tcol:"ts"})
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            return df[["ts","open","high","low","close","volume"]].dropna()
        except Exception:
            continue
    return pd.DataFrame()

def _download_yahoo_chart(symbol: str, range_: str, interval: str) -> pd.DataFrame:
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_, "interval": interval, "includePrePost": "false", "events": "div,splits"}
    try:
        r = _session.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return pd.DataFrame()
        res = result[0]
        ts = res.get("timestamp") or []
        ind = (res.get("indicators") or {}).get("quote") or []
        if not ts or not ind:
            return pd.DataFrame()
        q = ind[0] or {}
        df = pd.DataFrame({
            "ts": [datetime.fromtimestamp(t, tz=timezone.utc) for t in ts],
            "open": q.get("open"),
            "high": q.get("high"),
            "low": q.get("low"),
            "close": q.get("close"),
            "volume": q.get("volume"),
        }).dropna()
        if df.empty:
            return pd.DataFrame()
        return df[["ts","open","high","low","close","volume"]]
    except Exception:
        return pd.DataFrame()

def _download_yf(symbol: str, period: str, interval: str) -> pd.DataFrame:
    if not _YF_ENABLE:
        return pd.DataFrame()
    try:
        import yfinance as yf, logging
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        df = yf.download(symbol, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"}).reset_index()
        if "Datetime" in df.columns:
            df = df.rename(columns={"Datetime":"ts"})
        elif "Date" in df.columns:
            df = df.rename(columns={"Date":"ts"})
        else:
            df["ts"] = df.index
        return df[["ts","open","high","low","close","volume"]]
    except Exception:
        return pd.DataFrame()

def fetch_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    symbol = symbol.upper().strip()
    outputsize = 500 if timeframe == "1d" else 300
    df = _download_td(symbol, timeframe, outputsize)
    if not df.empty:
        return df
    if timeframe == "1h":
        df = _download_yahoo_chart(symbol, range_="7d", interval="60m")
        if df.empty:
            df = _download_yahoo_chart(symbol, range_="1y", interval="1d")
    elif timeframe == "1d":
        df = _download_yahoo_chart(symbol, range_="1y", interval="1d")
        if df.empty:
            df = _download_yahoo_chart(symbol, range_="2y", interval="1d")
    else:
        raise ValueError("Unsupported timeframe")
    if not df.empty:
        return df
    if timeframe == "1h":
        df = _download_yf(symbol, period="7d", interval="60m")
        if df.empty:
            df = _download_yf(symbol, period="1y", interval="1d")
        return df
    else:
        return _download_yf(symbol, period="1y", interval="1d")

def last_price(symbol: str) -> Optional[float]:
    """Greitas paskutinės kainos gavimas per Yahoo Chart (keletas bandymų)."""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol.upper().strip()}"
    tries = [("1d","1m"), ("5d","1h"), ("1y","1d")]
    for rng, itv in tries:
        try:
            r = _session.get(url, params={"range": rng, "interval": itv, "includePrePost":"false"}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            res = (data.get("chart") or {}).get("result") or []
            if not res: 
                continue
            q = (res[0].get("indicators") or {}).get("quote") or []
            if not q: 
                continue
            closes = (q[0] or {}).get("close") or []
            for val in reversed(closes):
                if isinstance(val, (int,float)) and val == val:
                    return float(val)
        except Exception:
            continue
    return None
