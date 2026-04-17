import aiohttp
import asyncio
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

class TelegramAlert:
    def __init__(self):
        # โหลด Queue ในจังหวะที่รัน Event Loop แล้วเท่านั้น ป้องกันโปรแกรมค้าง
        self.queue = None 
        self.is_running = False
        self.worker_task = None

    async def start_worker(self):
        if self.queue is None:
            self.queue = asyncio.Queue(maxsize=20)
            
        self.is_running = True
        async with aiohttp.ClientSession() as session:
            while self.is_running:
                try:
                    msg = await self.queue.get()
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
                    
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 429: # จัดการ Rate Limit ของ Telegram
                            error_data = await resp.json()
                            retry_after = error_data.get("parameters", {}).get("retry_after", 5)
                            print(f"⚠️ [Telegram] โดน Rate Limit พักเบรก {retry_after} วินาที...")
                            await asyncio.sleep(retry_after)
                        elif resp.status != 200:
                            err_msg = await resp.text()
                            print(f"❌ [Telegram API Error] ส่งข้อความไม่ผ่าน: {err_msg}")
                    
                    self.queue.task_done()
                    await asyncio.sleep(1.5) # Rate Limit เบื้องต้น
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"❌ [Telegram Network Error]: {e}")
                    await asyncio.sleep(2)

    async def send(self, message: str):
        if self.queue is None:
            self.queue = asyncio.Queue(maxsize=20)
            
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            print(f"⚠️ [Telegram Queue Full] ทิ้งข้อความเพื่อป้องกัน Flood: {message[:20]}...")

    async def close(self):
        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
        print("🔒 [Telegram] Worker closed.")