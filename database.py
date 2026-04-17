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
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data:
                        print("⚠️ [Database] ตาราง bot_state ว่างเปล่า! (คุณลืมกด Insert Row เพื่อสร้าง id=1 หรือเปล่า?)")
                    return data[0] if data else None
                else:
                    err_msg = await resp.text()
                    print(f"❌ [Database Read Error] Status {resp.status}: {err_msg}")
            return None
        except Exception as e:
            print(f"❌ [Database Crash]: {e}")
            return None

    async def update_state(self, payload: dict):
        try:
            session = await self.get_session()
            url = f"{SUPABASE_URL}/rest/v1/bot_state?id=eq.1"
            async with session.patch(url, json=payload, timeout=10) as resp:
                if resp.status not in [200, 204]:
                    err_msg = await resp.text()
                    print(f"❌ [Database Write Error] Status {resp.status}: {err_msg}")
                    return False
                return True
        except Exception as e:
            print(f"❌ [Database Crash]: {e}")
            return False