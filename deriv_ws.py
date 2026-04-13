import websockets
import json
import asyncio
from config import DERIV_APP_ID, DERIV_TOKEN

class DerivWS:
    def __init__(self, telegram_bot):
        self.url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        self.ws = None
        self.telegram = telegram_bot

    async def connect(self):
        retries = 0
        while True:
            try:
                self.ws = await websockets.connect(self.url, ping_interval=30, ping_timeout=10)
                await self.send({"authorize": DERIV_TOKEN})
                await self.telegram.send("✅ Connected to Deriv API")
                return True
            except websockets.exceptions.InvalidStatusCode as e:
                if e.status_code in [502, 503]:
                    await self.telegram.send(f"⚠️ Deriv Server Maintenance ({e.status_code}). Sleeping for 10 mins...")
                    await asyncio.sleep(600) # หลับ 10 นาที
                else:
                    raise e
            except Exception as e:
                wait_time = min(2 ** retries, 60)
                print(f"Connection lost. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                retries += 1

    async def send(self, payload: dict):
        if self.ws:
            await self.ws.send(json.dumps(payload))

    async def receive(self):
        if self.ws:
            response = await self.ws.recv()
            return json.loads(response)