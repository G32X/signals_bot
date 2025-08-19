from telegram.ext import Application, CommandHandler
from .config import settings

async def start(update, context):
    await update.message.reply_text("Hello, I'm your trading bot!")

def run_bot():
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
