from telegram.ext import Application, CommandHandler
from telegram import Update
from typing import Final
import asyncio
import logging

from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tg-bot")


async def cmd_start(update: Update, context):
    await update.message.reply_text(
        "👋 Tech Signals Bot online.\n"
        "Komandos: /help, /status"
    )

async def cmd_help(update: Update, context):
    await update.message.reply_text(
        "🧭 Komandos:\n"
        "/start – pasisveikinimas\n"
        "/help – pagalba\n"
        "/status – boto būsena"
    )

async def cmd_status(update: Update, context):
    await update.message.reply_text("✅ Botas veikia (worker) – Railway.")


def build_app() -> Application:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nėra nustatytas")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    return app


def run():
    app = build_app()
    # PTB v21: run_polling yra blokavimo metodas (startuoja event loop viduje)
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    run()
