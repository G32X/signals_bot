# app/universe.py
from __future__ import annotations
from typing import List, Dict, Any
import time
import math
import requests

from .config import settings

TD_KEY = settings.TWELVEDATA_API_KEY.strip()
BASE = "https://api.twelvedata.com"

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Connection": "keep-alive",
})

# Paprastas limiteris – tas pats principas kaip data.py, bet atskiras čia,
# kad nebūtų ciklinių importų.
_minute_start = 0.0
_minute_count = 0
_day_start = 0.0
_day_count = 0

def _now() -> float:
    return time.time()

def _td_allow() -> bool:
    global _minute_start, _minute_count, _day_start, _day_count
    t = _now()
    if t - _minute_start >= 60:
        _minute_start = t
        _minute_count = 0
    if t - _day_start >= 86400:
        _day_start = t
        _day_count = 0
    if _minute_count < settings.TD_MAX_PER_MINUTE and _day_count < settings.TD_MAX_PER_DAY:
        _minute_count += 1
        _day_count += 1
        return True
    return False

def _throttle_sleep():
    """Jei viršijam per minutę – miegam iki naujo lango."""
    global _minute_start, _minute_count
    t = _now()
    wait = max(0.0, 60 - (t - _minute_start))
    if wait > 0 and _minute_count >= settings.TD_MAX_PER_MINUTE:
        time.sleep(wait)

def _get(path: str, params: Dict[str, Any]) -> Dict[str, Any] | None:
    if not _td_allow():
        _throttle_sleep()
        if not _td_allow():
            # jeigu ir po miego negalim – vėliau bandysim grįžti caller'yje
            return None
    try:
        r = _session.get(f"{BASE}{path}", params=params, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _list_exchange_symbols(exchange: str) -> List[str]:
    """Gražina visus simbolius iš nurodyto mainų sąrašo (US)."""
    out: List[str] = []
    page = 1
    while True:
        data = _get("/stocks", {
            "exchange": exchange,
            "country": "United States",
            "format": "JSON",
            "page": page,
            "apikey": TD_KEY,
        })
        if not data:
            break
        # TwelveData gali grąžinti: {"data":[{symbol:..., name:..., exchange:..., currency:..., ...}], "next_page":2}
        items = data.get("data") or data.get("symbols") or data.get("values") or []
        if not items:
            break
        for it in items:
            sym = (it.get("symbol") or "").strip().upper()
            if sym:
                out.append(sym)
        nxt = data.get("next_page")
        if not nxt:
            break
        page = nxt
        # Saugiai
        if page > 200:
            break
    return out

def _profile(symbol: str) -> Dict[str, Any] | None:
    """Gauti profilį – sektorius, šalis, market cap."""
    data = _get("/profile", {"symbol": symbol, "apikey": TD_KEY})
    if not data:
        return None
    # Tikėtini laukai: 'sector', 'country', 'market_cap' (arba 'market_capitalization')
    return data

def _parse_market_cap(val: Any) -> float | None:
    if val is None:
        return None
    # Gali būti string "123456789" ar float/int
    try:
        return float(val)
    except Exception:
        # kartais būna kaip "123.45M" ar "1.2B"
        s = str(val).strip().upper().replace(",", "")
        mult = 1.0
        if s.endswith("B"):
            mult = 1_000_000_000.0
            s = s[:-1]
        elif s.endswith("M"):
            mult = 1_000_000.0
            s = s[:-1]
        elif s.endswith("K"):
            mult = 1_000.0
            s = s[:-1]
        try:
            return float(s) * mult
        except Exception:
            return None

def fetch_tech_microcaps(limit_cap: int = 300_000_000) -> List[str]:
    """
    Surenka visus JAV (United States) technologijų sektoriaus simbolius
    (NASDAQ, NYSE, AMEX) ir filtruoja market cap < limit_cap.
    Naudoja TwelveData /stocks + /profile. Gerbia TD rate limitus.
    """
    if not TD_KEY:
        # be raktų – nieko negrąžinam
        return []

    exchanges = ["NASDAQ", "NYSE", "AMEX"]
    symbols: List[str] = []
    for ex in exchanges:
        syms = _list_exchange_symbols(ex)
        symbols.extend(syms)

    symbols = list(dict.fromkeys(symbols))  # uniq, preserve order
    selected: List[str] = []

    for i, sym in enumerate(symbols, 1):
        p = _profile(sym)
        if not p:
            continue

        # TwelveData /profile formatai gali skirtis, pabandome kelis raktus
        sector = (p.get("sector") or p.get("Sector") or "").strip()
        country = (p.get("country") or p.get("Country") or "").strip()
        mc = _parse_market_cap(p.get("market_cap") or p.get("market_capitalization") or p.get("Market Capitalization"))

        if sector.lower() == "technology" and country.lower() in ("united states", "usa", "us"):
            if mc is not None and mc < float(limit_cap):
                selected.append(sym)

        # Kad neperžengtume min limitų – pritaikom saugiklius
        if i % settings.TD_MAX_PER_MINUTE == 0:
            _throttle_sleep()

        # Papildomas saugiklis nuo bereikalingo ilgo skenavimo:
        # jeigu jau surinkom > 2000 mikrocap'ų — stabdom (labai daug realiai nereikės).
        if len(selected) > 2000:
            break

    return selected
