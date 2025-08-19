import asyncio
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import settings
from .db import SessionLocal
from .models import Signal, SignalStatus, Watchlist, Position, PositionStatus

@dataclass
class TelegramDB:
    subscribers: set[int]

def _fmt_pct(x: float) -> str:
    return f"{x*100:.2f}%"

def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

_http_session = requests.Session()
_http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Connection": "keep-alive",
})

def _latest_price(symbol: str) -> Optional[float]:
    symbol = symbol.upper().strip()
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    tries = [
        {"range": "1d", "interval": "1m"},
        {"range": "5d", "interval": "1h"},
        {"range": "1y", "interval": "1d"},
    ]
    for p in tries:
        try:
            r = _http_session.get(url, params={
                "range": p["range"], "interval": p["interval"],
                "includePrePost": "false", "events": "div,splits",
            }, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
            result = (data.get("chart") or {}).get("result") or []
            if not result:
                continue
            res = result[0]
            ind = (res.get("indicators") or {}).get("quote") or []
            if not ind:
                continue
            closes = (ind[0] or {}).get("close") or []
            for val in reversed(closes):
                if isinstance(val, (int, float)) and val == val:
                    return float(val)
        except Exception:
            continue
    return None

class BotInstance:
    def __init__(self):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        self.app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        self.db = TelegramDB(subscribers=set())
        self._configure_handlers()

    def _configure_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("subscribe", self.subscribe))
        self.app.add_handler(CommandHandler("unsubscribe", self.unsubscribe))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("ping", self.ping))
        self.app.add_handler(CommandHandler("signals", self.signals_overview))
        self.app.add_handler(CommandHandler("last", self.last_signal))
        self.app.add_handler(CommandHandler("pnl", self.pnl))
        self.app.add_handler(CommandHandler("watchlist", self.watchlist))
        self.app.add_handler(CommandHandler("positions", self.positions))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 Welcome to Tech Signals Bot!\n"
            "Commands: /help\n"
            "/watchlist [show|add|remove] – manage watchlist\n"
            "/positions – open long positions"
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🧭 Commands\n"
            "/start – welcome & link\n"
            "/subscribe – receive signal alerts\n"
            "/unsubscribe – stop alerts\n"
            "/status – bot subscriber count\n"
            "/ping – health and time\n"
            "/signals – overview of recent signals\n"
            "/last <SYMBOL> [TF] – latest signal (1h|1d)\n"
            "/pnl [N] – unrealized P&L across last N signals (default 20)\n"
            "/watchlist [show|add|remove]\n"
            "/positions – open long positions"
        )

    async def subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.db.subscribers.add(chat_id)
        await update.message.reply_text("✅ Subscribed to signal alerts. Use /unsubscribe to stop.")

    async def unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.db.subscribers.discard(chat_id)
        await update.message.reply_text("🛑 Unsubscribed.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Subscribers: {len(self.db.subscribers)}")

    async def ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"pong • {_now_utc_str()} • app={settings.APP_NAME}")

    async def signals_overview(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            from_24h = now - timedelta(hours=24)
            from_7d = now - timedelta(days=7)
            count_24h = db.query(Signal).filter(Signal.created_at >= from_24h).count()
            count_7d = db.query(Signal).filter(Signal.created_at >= from_7d).count()
            recent = db.query(Signal).order_by(Signal.created_at.desc()).limit(5).all()
            lines = [f"📊 Signals overview", f"Last 24h: {count_24h}", f"Last 7d: {count_7d}"]
            if recent:
                lines.append("Recent:")
                for r in recent:
                    lines.append(
                        f"• {r.symbol} {r.timeframe} {r.direction} @ {r.entry} | SL {r.stop} | TP1 {r.tp1} | RR {r.rr} ({r.confidence})"
                    )
            await update.message.reply_text("\n".join(lines))
        finally:
            db.close()

    async def last_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /last <SYMBOL> [TF] e.g. /last AAPL 1h")
            return
        symbol = args[0].upper()
        tf = args[1] if len(args) > 1 else None
        db = SessionLocal()
        try:
            q = db.query(Signal).filter(Signal.symbol == symbol)
            if tf:
                q = q.filter(Signal.timeframe == tf)
            row = q.order_by(Signal.created_at.desc()).first()
            if not row:
                await update.message.reply_text(f"No signals found for {symbol}{' '+tf if tf else ''}.")
                return
            txt = (
                f"[{row.direction}] {row.symbol} ({row.timeframe})\n"
                f"Entry: {row.entry} | SL: {row.stop} | TP1: {row.tp1} | TP2: {row.tp2}\n"
                f"Reason: {row.reason}\nConfidence: {row.confidence} | R:R: {row.rr}\n"
                f"Time: {row.created_at:%Y-%m-%d %H:%M UTC}"
            )
            await update.message.reply_text(txt)
        finally:
            db.close()

    async def pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            N = int(context.args[0]) if context.args else 20
        except Exception:
            N = 20
        db = SessionLocal()
        try:
            signals = db.query(Signal).order_by(Signal.created_at.desc()).limit(N).all()
            if not signals:
                await update.message.reply_text("No signals to evaluate.")
                return
            total_pct = 0.0
            wins = 0
            evaluated = 0
            parts = []
            for s in signals:
                # P/L nuo paskutinės kainos
                price = _latest_price(s.symbol)
                if price is None or s.entry == 0:
                    continue
                change = (price - s.entry) / s.entry if s.direction == "BUY" else (s.entry - price) / s.entry
                total_pct += change
                evaluated += 1
                wins += 1 if change > 0 else 0
                parts.append(f"• {s.symbol} {s.timeframe} {s.direction}: {(change*100):.2f}% (entry {s.entry} → last {price:.2f})")
            if evaluated == 0:
                await update.message.reply_text("Could not fetch prices right now. Try again later.")
                return
            avg = total_pct / evaluated
            win_rate = wins / evaluated
            header = f"💹 Unrealized P&L (last {evaluated} signals)\nAvg: {(avg*100):.2f}% | Win rate: {(win_rate*100):.2f}%"
            await update.message.reply_text("\n".join([header] + parts[:20]))
        finally:
            db.close()

    async def watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        subcmd = (context.args[0].lower() if context.args else "show")
        db = SessionLocal()
        try:
            if subcmd == "show":
                rows = db.query(Watchlist).order_by(Watchlist.symbol.asc()).all()
                syms = [r.symbol for r in rows]
                if not syms:
                    await update.message.reply_text("Watchlist is empty.")
                else:
                    out, chunk = [], []
                    for s in syms:
                        chunk.append(s)
                        if len(chunk) == 25:
                            out.append(", ".join(chunk)); chunk = []
                    if chunk: out.append(", ".join(chunk))
                    await update.message.reply_text("👀 Watchlist:\n" + "\n".join(out))
            elif subcmd == "add":
                syms = [s.upper() for s in context.args[1:]]
                if not syms:
                    await update.message.reply_text("Usage: /watchlist add SYMBOL [SYMBOL2 ...]"); return
                added = 0
                for s in syms:
                    if not db.query(Watchlist).filter_by(symbol=s).first():
                        db.add(Watchlist(symbol=s)); added += 1
                db.commit()
                await update.message.reply_text(f"✅ Added {added} symbol(s). Use /watchlist show to view.")
            elif subcmd == "remove":
                syms = [s.upper() for s in context.args[1:]]
                if not syms:
                    await update.message.reply_text("Usage: /watchlist remove SYMBOL [SYMBOL2 ...]"); return
                removed = 0
                for s in syms:
                    row = db.query(Watchlist).filter_by(symbol=s).first()
                    if row:
                        db.delete(row); removed += 1
                db.commit()
                await update.message.reply_text(f"🗑️ Removed {removed} symbol(s).")
            else:
                await update.message.reply_text("Usage: /watchlist [show|add|remove] ...")
        finally:
            db.close()

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = SessionLocal()
        try:
            rows = db.query(Position).filter_by(status=PositionStatus.OPEN).order_by(Position.opened_at.asc()).all()
            if not rows:
                await update.message.reply_text("No open positions."); return
            out = ["📌 Open positions:"]
            for r in rows:
                out.append(f"• {r.symbol} {r.timeframe} @ {r.entry} | SL {r.stop} | TP1 {r.tp1} | TP2 {r.tp2} (since {r.opened_at:%Y-%m-%d})")
            await update.message.reply_text("\n".join(out))
        finally:
            db.close()

    async def broadcast(self, text: str):
        for chat_id in list(self.db.subscribers):
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                pass

    async def run_polling(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def shutdown(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

bot_instance = BotInstance()

# ← paleidimas kaip atskiro worker'io
if __name__ == "__main__":
    asyncio.run(bot_instance.run_polling())
