import aiohttp
import asyncio
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

class TelegramAlert:
    def __init__(self):
        # โหลด Queue ในจังหวะที่รัน Event Loop แล้วเท่านั้น ป้องกันโปรแกรมค้าง
        self.queue = None 
        self.is_running = False

    async def start_worker(self):
        if self.queue is None:
            self.queue = asyncio.Queue()
            
        self.is_running = True
        async with aiohttp.ClientSession() as session:
            while self.is_running:
                msg = await self.queue.get()
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
                try:
                    async with session.post(url, json=payload) as resp:
                        if resp.status != 200:
                            err_msg = await resp.text()
                            print(f"❌ [Telegram API Error] ส่งข้อความไม่ผ่าน: {err_msg}")
                except Exception as e:
                    print(f"❌ [Telegram Network Error]: {e}")
                
                self.queue.task_done()
                await asyncio.sleep(2) # Rate Limit

    async def send(self, message: str):
        if self.queue is None:
            self.queue = asyncio.Queue()
        await self.queue.put(message)