import aiohttp
import asyncio
from config import SUPABASE_URL, SUPABASE_KEY

class SupabaseDB:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SupabaseDB, cls).__new__(cls)
            cls._instance.headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            cls._instance.session = None
        return cls._instance

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def get_state(self):
        try:
            session = await self.get_session()
            url = f"{SUPABASE_URL}/rest/v1/bot_state?select=*&id=eq.1"
            # เพิ่ม timeout=10 ป้องกันการค้างเวลารอโหลด State
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0] if data else None
            return None
        except Exception as e:
            print(f"Database Read Error: {e}")
            return None

    async def update_state(self, payload: dict):
        try:
            session = await self.get_session()
            url = f"{SUPABASE_URL}/rest/v1/bot_state?id=eq.1"
            # เพิ่ม timeout=10 ป้องกันการค้างเวลาเซฟ State
            async with session.patch(url, json=payload, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"Database Write Error: {e}")
            # ส่ง False กลับไป ดีกว่าปล่อยให้แอปค้างหรือแครช
            return False