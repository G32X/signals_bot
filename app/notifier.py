from telegram import Bot

class Notifier:
    def __init__(self, token: str):
        self.bot = Bot(token)

    async def notify(self, chat_id: int, message: str):
        await self.bot.send_message(chat_id=chat_id, text=message)
