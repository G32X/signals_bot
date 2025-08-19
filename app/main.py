from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List
import asyncio

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import Base, engine, get_db, SessionLocal
from .models import Signal, Watchlist, Position, PositionStatus
from .schemas import SignalOut, PositionOut, WatchlistOut, PortfolioRow
from .signal_engine import compute_entry, compute_exit
from .notifier import notify_signal
from .telegram_bot import bot_instance
from .universe import fetch_tech_microcaps
from .data import fetch_ohlcv, last_price


# --- Helperiai / bendros reikmės -------------------------------------------------

timeframes = [t.strip() for t in settings.DEFAULT_TIMEFRAMES.split(",") if t.strip()]

def get_watchlist_symbols(db: Session) -> List[str]:
    rows = db.query(Watchlist).order_by(Watchlist.symbol.asc()).all()
    return [r.symbol for r in rows]

def seed_watchlist_if_empty(db: Session):
    if db.query(Watchlist).count() == 0:
        defaults = [s.strip().upper() for s in settings.DEFAULT_WATCHLIST.split(",") if s.strip()]
        for s in defaults:
            db.add(Watchlist(symbol=s))
        db.commit()

def refresh_watchlist_from_twelvedata(db: Session, cap_limit: int) -> int:
    """
    Užpildo watchlist visomis JAV technologijų įmonėmis, kurių market cap < cap_limit.
    Duomenys imami iš TwelveData (stocks + profile). PERRAŠO esamą sąrašą.
    """
    syms = fetch_tech_microcaps(limit_cap=cap_limit)
    if not syms:
        return 0
    db.query(Watchlist).delete()
    for s in syms:
        db.add(Watchlist(symbol=s))
    db.commit()
    return len(syms)


# --- SCAN logika (apibrėžta PRIEŠ scheduler’į!) ---------------------------------

async def run_scan(db: Session) -> int:
    """
    Peržiūri watchlist simbolius ir timeframe'us.
    - Jei yra atvira pozicija → tikrina EXIT signalą (SELL).
    - Jei nėra atviros pozicijos → ieško ENTRY signalo (BUY) ir atidaro poziciją.
    Sukurtus signalus siunčia į Telegram per notify_signal().
    """
    created = 0
    wls = get_watchlist_symbols(db)
    for sym in wls:
        for tf in timeframes:
            # EXIT patikra (jei yra atvira pozicija)
            pos = (
                db.query(Position)
                .filter_by(symbol=sym, timeframe=tf, status=PositionStatus.OPEN)
                .order_by(Position.opened_at.desc())
                .first()
            )
            if pos:
                try:
                    exit_sig = compute_exit(sym, tf, pos.entry, pos.stop, pos.tp1, pos.tp2)
                except Exception:
                    exit_sig = None
                if exit_sig:
                    row = Signal(**{k: v for k, v in exit_sig.items() if k != "is_exit"})
                    db.add(row)
                    pos.status = PositionStatus.CLOSED
                    pos.closed_at = datetime.utcnow()
                    db.commit()
                    db.refresh(row)
                    created += 1
                    payload = SignalOut.model_validate(row).model_dump()
                    payload["notes"] = "EXIT"
                    await notify_signal(payload)
                continue

            # ENTRY paieška (jei nėra atviros pozicijos)
            try:
                entry_sig = compute_entry(sym, tf)
            except Exception:
                entry_sig = None
            if entry_sig:
                row = Signal(**entry_sig)
                db.add(row)
                db.commit()
                db.refresh(row)
                created += 1
                db.add(
                    Position(
                        symbol=sym,
                        timeframe=tf,
                        entry=entry_sig["entry"],
                        stop=entry_sig["stop"],
                        tp1=entry_sig["tp1"],
                        tp2=entry_sig["tp2"],
                    )
                )
                db.commit()
                await notify_signal(SignalOut.model_validate(row).model_dump())
    return created

async def job_scan_1h():
    db = SessionLocal()
    try:
        await run_scan(db)
    finally:
        db.close()

async def job_scan_1d():
    db = SessionLocal()
    try:
        await run_scan(db)
    finally:
        db.close()


# --- Lifespan (DB create_all, seed, scheduler, telegram bot) ---------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB schema
    Base.metadata.create_all(bind=engine)

    # Seed'inam watchlist, jei tuščia
    _db = SessionLocal()
    try:
        seed_watchlist_if_empty(_db)
        if settings.WATCHLIST_REFRESH_ON_START and settings.AUTO_FILTER_TECH and settings.TWELVEDATA_API_KEY:
            try:
                refresh_watchlist_from_twelvedata(_db, settings.MARKETCAP_LIMIT)
            except Exception:
                pass
    finally:
        _db.close()

    # Scheduler
    app.state.scheduler = None
    if settings.ENABLE_SCHEDULER:
        sched = AsyncIOScheduler(timezone=settings.TIMEZONE)
        # ✔ čia vardai jau apibrėžti aukščiau
        sched.add_job(job_scan_1h, CronTrigger.from_crontab(settings.SCHED_CRON_1H))
        sched.add_job(job_scan_1d, CronTrigger.from_crontab(settings.SCHED_CRON_1D))
        sched.start()
        app.state.scheduler = sched

    # Telegram botas (viename procese su web)
    app.state.bot_task = None
    if settings.ENABLE_TELEGRAM:
        async def _run_bot():
            try:
                await bot_instance.run_polling()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        app.state.bot_task = asyncio.create_task(_run_bot())

    try:
        yield
    finally:
        # Tel. bot shutdown
        if settings.ENABLE_TELEGRAM and app.state.bot_task:
            try:
                await asyncio.wait_for(bot_instance.shutdown(), timeout=settings.SHUTDOWN_TIMEOUT_SECONDS)
            except Exception:
                pass
            app.state.bot_task.cancel()
            try:
                await app.state.bot_task
            except Exception:
                pass

        # Scheduler shutdown
        if settings.ENABLE_SCHEDULER and app.state.scheduler:
            try:
                app.state.scheduler.shutdown(wait=False)
            except Exception:
                pass


# --- FastAPI app ----------------------------------------------------------------

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

@app.get("/health")
def health():
    return {"ok": True}


# --- API: signals / positions / watchlist / portfolio ---------------------------

@app.get("/api/signals", response_model=list[SignalOut])
def list_signals(
    limit: int = 200,
    symbol: str | None = None,
    timeframe: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Signal).order_by(Signal.created_at.desc())
    if symbol:
        q = q.filter(Signal.symbol == symbol.upper())
    if timeframe:
        q = q.filter(Signal.timeframe == timeframe)
    return q.limit(limit).all()

@app.get("/api/positions", response_model=list[PositionOut])
def list_positions(status: str = "OPEN", db: Session = Depends(get_db)):
    if status not in ("OPEN", "CLOSED"):
        status = "OPEN"
    return (
        db.query(Position)
        .filter_by(status=status)
        .order_by(Position.opened_at.desc())
        .all()
    )

@app.get("/api/watchlist", response_model=list[WatchlistOut])
def list_watchlist(db: Session = Depends(get_db)):
    return db.query(Watchlist).order_by(Watchlist.symbol.asc()).all()

class WatchlistIn(BaseModel):
    symbol: str
    @field_validator("symbol")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip().upper()
        if not v or any(ch.isspace() for ch in v):
            raise ValueError("Invalid symbol")
        return v

@app.post("/api/watchlist", response_model=WatchlistOut)
def add_watchlist(
    item: WatchlistIn,
    x_admin_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    existing = db.query(Watchlist).filter_by(symbol=item.symbol).first()
    if existing:
        return existing
    row = Watchlist(symbol=item.symbol)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

@app.delete("/api/watchlist/{symbol}")
def delete_watchlist(
    symbol: str,
    x_admin_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    symbol = symbol.strip().upper()
    row = db.query(Watchlist).filter_by(symbol=symbol).first()
    if not row:
        return {"deleted": 0}
    db.delete(row)
    db.commit()
    return {"deleted": 1}

@app.get("/api/ohlcv")
def api_ohlcv(symbol: str = Query(...), timeframe: str = Query("1d"), limit: int = 300):
    df = fetch_ohlcv(symbol.upper(), timeframe)
    if df.empty:
        return JSONResponse({"symbol": symbol.upper(), "timeframe": timeframe, "bars": []})
    df = df.tail(limit)
    bars = [
        {
            "time": int(row["ts"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0)),
        }
        for _, row in df.iterrows()
    ]
    return {"symbol": symbol.upper(), "timeframe": timeframe, "bars": bars}

@app.get("/api/portfolio", response_model=list[PortfolioRow])
def api_portfolio(db: Session = Depends(get_db)):
    rows = db.query(Position).filter_by(status=PositionStatus.OPEN).all()
    out: list[PortfolioRow] = []
    for r in rows:
        lp = last_price(r.symbol)
        ch = ((lp - r.entry) / r.entry) if (lp and r.entry) else None
        rr = ((r.tp1 - r.entry) / (r.entry - r.stop)) if r.entry > r.stop else None
        out.append(PortfolioRow(symbol=r.symbol, timeframe=r.timeframe, entry=float(r.entry), last=(float(lp) if lp else None), change_pct=(float(ch) if ch is not None else None), rr=(float(rr) if rr is not None else None)))
    return out


# --- ADMIN endpoint’ai -----------------------------------------------------------

@app.post("/admin/scan_now")
async def admin_scan_now(x_admin_token: str = Header(default="")):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db = SessionLocal()
    try:
        created = await run_scan(db)
        return {"queued": True, "signals_created": created}
    finally:
        db.close()

@app.post("/admin/refresh_microcaps")
def admin_refresh_microcaps(x_admin_token: str = Header(default="")):
    """
    Perrašo watchlist visomis US Technology įmonėmis su market cap < settings.MARKETCAP_LIMIT.
    Reikia TWELVEDATA_API_KEY.
    """
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not settings.TWELVEDATA_API_KEY:
        raise HTTPException(status_code=400, detail="TWELVEDATA_API_KEY is not set")
    db = SessionLocal()
    try:
        n = refresh_watchlist_from_twelvedata(db, settings.MARKETCAP_LIMIT)
        return {"updated": n}
    finally:
        db.close()

@app.post("/admin/reset_db")
def admin_reset_db(x_admin_token: str = Header(default="")):
    """
    DĖMESIO: Ištrina VISAS lenteles ir sukuria iš naujo pagal esamus modelius.
    Naudoti tik jei schema susimaišė (pvz., UndefinedColumn klaidos).
    """
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS positions CASCADE;")
        conn.exec_driver_sql("DROP TABLE IF EXISTS signals CASCADE;")
        conn.exec_driver_sql("DROP TABLE IF EXISTS watchlist CASCADE;")
        # optional: enum tipų drop, jei buvo sukūrę:
        try:
            conn.exec_driver_sql("DROP TYPE IF EXISTS signalstatus CASCADE;")
        except Exception:
            pass
        try:
            conn.exec_driver_sql("DROP TYPE IF EXISTS positionstatus CASCADE;")
        except Exception:
            pass
    Base.metadata.create_all(bind=engine)
    return {"reset": True}


# --- UI (grafinė sąsaja su grafiku ir t.t.) -------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    options = "".join([f'<option value="{tf}">{tf}</option>' for tf in timeframes])
    app_name = settings.APP_NAME
    tfs = ", ".join(timeframes)

    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>[[APP_NAME]]</title>
  <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
  <style>
    :root { --bg:#0b0d12; --panel:#111827; --border:#1f2937; --text:#e5e7eb; --muted:#9ca3af; --accent:#3b82f6; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    header { padding: 1rem 1.2rem; border-bottom:1px solid var(--border); position:sticky; top:0; background:rgba(11,13,18,0.9); backdrop-filter: blur(6px); }
    main { display:grid; grid-template-columns: 340px 1fr; gap: 1rem; padding:1rem; }
    .card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:1rem; }
    .pill { border-radius:999px; padding:0.15rem 0.6rem; border:1px solid var(--border); font-size:12px; color:var(--muted); }
    .row { display:flex; gap:.5rem; align-items:center; }
    .list { display:flex; flex-direction:column; gap:.5rem; max-height: 45vh; overflow:auto; }
    .item { display:flex; justify-content:space-between; gap:.5rem; padding:.45rem .6rem; border:1px solid var(--border); border-radius:10px; cursor:pointer; }
    .item:hover { border-color:#334155; }
    #chart { height: 520px; }
    .grid2 { display:grid; grid-template-columns: 1fr 1fr; gap: .6rem; }
    .muted { color:var(--muted); }
    button, select, input { background:#0f172a; color:#e5e7eb; border:1px solid var(--border); border-radius:10px; padding:.35rem .6rem; }
    button { cursor:pointer; }
    button:hover { border-color:#334155; }
    input { width: 100%; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding:.4rem .5rem; border-bottom:1px solid var(--border); text-align:left; font-size: 14px; }
    th { color:#94a3b8; font-weight:600; }
    tr:hover td { background:#0e1628; }
    .green { color:#22c55e; } .red { color:#ef4444; }
    @media (max-width: 1000px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header class="row">
    <h3 style="margin:0;">[[APP_NAME]]</h3>
    <span class="pill">Timeframes: [[TFS]]</span>
  </header>
  <main>
    <aside class="card">
      <div class="row" style="justify-content:space-between;">
        <strong>Watchlist</strong>
        <select id="tf">[[OPTIONS]]</select>
      </div>
      <div id="watchlist" class="list"></div>

      <div class="card" style="margin-top:1rem;">
        <strong>Open positions</strong>
        <div id="positions" class="list"></div>
      </div>

      <div class="card" style="margin-top:1rem;">
        <strong>Manage watchlist</strong>
        <div class="row" style="margin-top:.5rem; gap:.4rem;">
          <input id="sym" placeholder="e.g. KVHI" style="flex:1;">
          <button id="btnAdd">Add</button>
          <button id="btnRemove">Remove</button>
        </div>
        <div class="row" style="margin-top:.5rem; gap:.4rem;">
          <input id="adm" placeholder="Admin token" style="flex:1;">
          <button id="btnSaveTok">Save token</button>
        </div>
        <div id="msg" class="muted" style="margin-top:.4rem;"></div>
      </div>
    </aside>

    <section>
      <div class="card">
        <div class="row" style="justify-content:space-between;">
          <div>
            <strong id="title">Chart</strong>
            <span class="pill" id="subtitle"></span>
            <span class="pill" id="last"></span>
          </div>
          <div class="row">
            <button id="reload">Reload</button>
          </div>
        </div>
        <div id="chart"></div>
      </div>
      <div class="grid2">
        <div class="card">
          <strong>Recent signals</strong>
          <div id="signals" class="list"></div>
        </div>
        <div class="card">
          <strong>Portfolio (P/L)</strong>
          <table>
            <thead>
              <tr><th>Symbol</th><th>TF</th><th>Entry</th><th>Last</th><th>P/L</th><th>R:R</th></tr>
            </thead>
            <tbody id="portfolio"></tbody>
          </table>
        </div>
      </div>
    </section>
  </main>

<script>
  const fmt2 = (n)=> (n===null||n===undefined? '—' : (Math.round(n*100)/100).toFixed(2));
  let currentSymbol = null;

  function getToken() { return localStorage.getItem("admin_token") || ""; }
  function setToken(v) { localStorage.setItem("admin_token", v || ""); }

  async function fetchJSON(url) { const r = await fetch(url); return await r.json(); }
  async function fetchJSONAuth(url, options = {}) {
    const token = getToken();
    options.headers = Object.assign({}, options.headers || {}, token ? {"X-Admin-Token": token} : {});
    const r = await fetch(url, options);
    if (!r.ok) {
      let t = "";
      try { t = (await r.json()).detail || r.statusText; } catch(e) { t = r.statusText; }
      throw new Error(t || `HTTP ${r.status}`);
    }
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return r.json();
    return r.text();
  }

  async function loadWatchlist() {
    const wl = await fetchJSON('/api/watchlist');
    const el = document.getElementById('watchlist');
    el.innerHTML = '';
    wl.forEach(w => {
      const div = document.createElement('div');
      div.className = 'item';
      div.innerHTML = `<span>${w.symbol}</span><span class="muted">›</span>`;
      div.onclick = () => selectSymbol(w.symbol);
      el.appendChild(div);
    });
    if (wl.length && !currentSymbol) selectSymbol(wl[0].symbol);
  }

  function buildChart(containerId) {
    const chart = LightweightCharts.createChart(document.getElementById(containerId), {
      layout: { background: { type:'Solid', color:'#111827' }, textColor:'#e5e7eb' },
      grid: { vertLines: { color:'#1f2937' }, horzLines: { color:'#1f2937' } },
      rightPriceScale: { borderColor:'#1f2937' },
      timeScale: { borderColor:'#1f2937', timeVisible:true },
      crosshair: { mode: 1 }
    });
    const candle = chart.addCandlestickSeries();
    const ema20 = chart.addLineSeries({ lineWidth: 2 });
    const ema50 = chart.addLineSeries({ lineWidth: 2 });
    return { chart, candle, ema20, ema50 };
  }

  const { chart, candle, ema20, ema50 } = buildChart('chart');

  function ema(data, len) {
    const k = 2/(len+1); let prev=null; const out=[];
    data.forEach((b)=> {
      const v=b.close;
      const e = prev===null? v : (v - prev)*k + prev;
      out.push({ time:b.time, value:e }); prev=e;
    });
    return out;
  }

  async function loadChart(symbol) {
    const tf = document.getElementById('tf').value;
    const o = await fetchJSON(`/api/ohlcv?symbol=${symbol}&timeframe=${tf}&limit=300`);
    document.getElementById('title').innerText = `${symbol}`;
    document.getElementById('subtitle').innerText = tf;
    const bars = o.bars.map(b=>({ time:b.time, open:b.open, high:b.high, low:b.low, close:b.close, volume:b.volume }));
    candle.setData(bars);
    ema20.setData(ema(bars, 20));
    ema50.setData(ema(bars, 50));
    if (bars.length) {
      const last = bars[bars.length-1].close;
      document.getElementById('last').innerText = `Last: $${fmt2(last)}`;
    }

    const sigs = await fetchJSON(`/api/signals?symbol=${symbol}&limit=50`);
    const el = document.getElementById('signals');
    el.innerHTML = '';
    sigs.forEach(s => {
      const it = document.createElement('div');
      it.className = 'item';
      it.innerHTML = `<div><b>${s.direction}</b> <span class="pill">${s.timeframe}</span> @ $${fmt2(s.entry)}</div>
                      <div class="muted">${new Date(s.created_at).toLocaleString()}</div>`;
      el.appendChild(it);
    });

    if (sigs.length) {
      const s = sigs[0];
      const mk = (price, color) => candle.createPriceLine({ price, color, lineWidth:1, lineStyle:0, axisLabelVisible:true });
      mk(s.entry, '#3b82f6'); mk(s.stop, '#ef4444'); mk(s.tp1, '#22c55e'); mk(s.tp2, '#22c55e');
    }
  }

  async function loadPositions() {
    const rows = await fetchJSON('/api/positions?status=OPEN');
    const el = document.getElementById('positions');
    el.innerHTML = '';
    rows.forEach(p => {
      const div = document.createElement('div');
      div.className = 'item';
      div.innerHTML = `<span>${p.symbol} <span class="pill">${p.timeframe}</span></span>
                       <span class="muted">@ $${fmt2(p.entry)}</span>`;
      div.onclick = () => selectSymbol(p.symbol);
      el.appendChild(div);
    });
  }

  async function loadPortfolio() {
    const rows = await fetchJSON('/api/portfolio');
    const body = document.getElementById('portfolio');
    body.innerHTML = '';
    rows.forEach(r => {
      const pnl = (r.change_pct===null? '—' : ((r.change_pct*100).toFixed(2) + '%'));
      const cls = (r.change_pct || 0) >= 0 ? 'green' : 'red';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${r.symbol}</td>
        <td>${r.timeframe}</td>
        <td>$${fmt2(r.entry)}</td>
        <td>$${fmt2(r.last)}</td>
        <td class="${cls}">${pnl}</td>
        <td>${r.rr === null ? '—' : r.rr.toFixed(2)}</td>
      `;
      body.appendChild(tr);
    });
  }

  async function selectSymbol(sym) {
    currentSymbol = sym;
    await loadChart(sym);
  }

  document.getElementById('reload').onclick = async ()=>{
    if (currentSymbol) await loadChart(currentSymbol);
    await loadPositions();
    await loadPortfolio();
    await loadWatchlist();
  };

  document.getElementById('tf').onchange = ()=> currentSymbol && loadChart(currentSymbol);

  const elSym = () => document.getElementById("sym");
  const elAdm = () => document.getElementById("adm");
  const elMsg = () => document.getElementById("msg");

  async function addSymbol() {
    const s = (elSym().value || "").trim().toUpperCase();
    if (!s) return;
    try {
      await fetchJSONAuth("/api/watchlist", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({symbol: s})
      });
      elMsg().innerText = `✅ Added ${s}`;
      elSym().value = "";
      await loadWatchlist();
    } catch (e) {
      elMsg().innerText = `⚠️ ${e.message || e}`;
    }
  }

  async function removeSymbol() {
    const s = (elSym().value || "").trim().toUpperCase();
    if (!s) return;
    try {
      const res = await fetchJSONAuth(`/api/watchlist/${encodeURIComponent(s)}`, { method: "DELETE" });
      elMsg().innerText = (res.deleted ? `🗑️ Removed ${s}` : `ℹ️ ${s} not found`);
      elSym().value = "";
      await loadWatchlist();
    } catch (e) {
      elMsg().innerText = `⚠️ ${e.message || e}`;
    }
  }

  function initTokenUI() {
    elAdm().value = getToken();
    document.getElementById("btnSaveTok").onclick = () => {
      setToken(elAdm().value.trim());
      elMsg().innerText = "🔐 Token saved locally.";
    };
    document.getElementById("btnAdd").onclick = addSymbol;
    document.getElementById("btnRemove").onclick = removeSymbol;
  }

  (async ()=>{
    initTokenUI();
    await loadWatchlist();
    await loadPositions();
    await loadPortfolio();
    setInterval(async ()=>{
      if (currentSymbol) await loadChart(currentSymbol);
      await loadPortfolio();
    }, 30000);
  })();
</script>
</body>
</html>
"""
    html = (
        html.replace("[[APP_NAME]]", app_name)
        .replace("[[TFS]]", tfs)
        .replace("[[OPTIONS]]", options)
    )
    return HTMLResponse(content=html)
