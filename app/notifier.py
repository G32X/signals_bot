# app/notifier.py
from typing import Dict, Any
from .telegram_bot import bot_instance

def _fmt_signal(payload: Dict[str, Any]) -> str:
    sym = payload.get("symbol", "?")
    tf = payload.get("timeframe", "?")
    direction = payload.get("direction", "?")
    entry = payload.get("entry")
    stop = payload.get("stop")
    tp1 = payload.get("tp1")
    tp2 = payload.get("tp2")
    rr = payload.get("rr")
    conf = payload.get("confidence", "")
    reason = payload.get("reason", "")
    notes = payload.get("notes", "")

    parts = [f"🔔 Signal: {sym} {tf} {direction}"]
    if entry is not None:
        parts.append(f"Entry: {entry:.2f}")
    if stop is not None:
        parts.append(f"SL: {stop:.2f}")
    if tp1 is not None:
        parts.append(f"TP1: {tp1:.2f}")
    if tp2 is not None:
        parts.append(f"TP2: {tp2:.2f}")
    if rr is not None:
        parts.append(f"R:R {rr}")
    if conf:
        parts.append(f"Conf: {conf}")
    if reason:
        parts.append(f"Reason: {reason}")
    if notes:
        parts.append(f"Notes: {notes}")
    return " | ".join(parts)

async def notify_signal(payload: Dict[str, Any]) -> None:
    """
    Broadcast signal to all subscribed Telegram chats.
    Uses app.telegram_bot.bot_instance.broadcast(...)
    """
    msg = _fmt_signal(payload)
    await bot_instance.broadcast(msg)
