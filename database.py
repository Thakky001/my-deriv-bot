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

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            print("🔒 [Database] Session closed properly.")

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

    # --- ฟังก์ชันใหม่ สำหรับระบบเก็บสถิติรายวันแยกตาราง ---
    async def get_all_daily_history(self):
        try:
            session = await self.get_session()
            url = f"{SUPABASE_URL}/rest/v1/daily_history?select=*"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    formatted_data = {}
                    for row in data:
                        formatted_data[row["date"]] = {
                            "profit": float(row.get("profit", 0.0)),
                            "wins": int(row.get("win_count", 0)),
                            "losses": int(row.get("loss_count", 0))
                        }
                    return formatted_data
                return {}
        except Exception as e:
            print(f"❌ [Database Daily Read Crash]: {e}")
            return {}

    async def update_daily_record(self, date_str: str, profit: float, win: int, loss: int):
        try:
            session = await self.get_session()
            url = f"{SUPABASE_URL}/rest/v1/daily_history?date=eq.{date_str}"
            
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        # มีข้อมูลของวันนี้แล้ว ให้นำค่ามาบวกเพิ่ม (PATCH)
                        curr = data[0]
                        new_payload = {
                            "profit": float(curr.get("profit", 0.0)) + profit,
                            "win_count": int(curr.get("win_count", 0)) + win,
                            "loss_count": int(curr.get("loss_count", 0)) + loss
                        }
                        async with session.patch(url, json=new_payload, timeout=10) as p_resp:
                            return new_payload["profit"]
                    else:
                        # ยังไม่มีข้อมูลของวันนี้ ให้สร้างแถวใหม่ (POST)
                        new_payload = {
                            "date": date_str,
                            "profit": profit,
                            "win_count": win,
                            "loss_count": loss
                        }
                        post_url = f"{SUPABASE_URL}/rest/v1/daily_history"
                        async with session.post(post_url, json=new_payload, timeout=10) as p_resp:
                            return profit
        except Exception as e:
            print(f"❌ [Database Daily Write Crash]: {e}")
            return 0.0