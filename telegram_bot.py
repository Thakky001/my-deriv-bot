import aiohttp
import asyncio
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

class TelegramAlert:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_running = False

    async def start_worker(self):
        self.is_running = True
        async with aiohttp.ClientSession() as session:
            while self.is_running:
                msg = await self.queue.get()
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
                try:
                    await session.post(url, json=payload)
                except Exception as e:
                    print(f"Telegram Error: {e}")
                
                self.queue.task_done()
                await asyncio.sleep(2) # Rate Limit: 1 ข้อความ / 2 วินาที

    async def send(self, message: str):
        await self.queue.put(message)