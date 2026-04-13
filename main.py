from fastapi import FastAPI
import asyncio
import pandas as pd
from deriv_ws import DerivWS
from telegram_bot import TelegramAlert
from database import SupabaseDB
from indicators import Strategy
from config import SYMBOL

app = FastAPI()
telegram = TelegramAlert()
db = SupabaseDB()
deriv = DerivWS(telegram)
strategy = Strategy()

bot_state = {"active_trade": False, "contract_id": None, "entry_price": 0, "sl": 0, "tp": 0, "is_breakeven": False}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram.start_worker())
    asyncio.create_task(trading_loop())

@app.get("/ping")
async def ping():
    return {"status": "alive"}

async def active_trade_manager(msg):
    """ จัดการ Break-even จาก Stream ของ proposal_open_contract """
    if "proposal_open_contract" in msg:
        contract = msg["proposal_open_contract"]
        if not contract:
            return

        profit = contract.get("profit", 0)
        current_price = contract.get("current_spot")
        
        # ถ้ายอด Profit มากกว่า 1.5 ATR (จุดคุ้มทุน) และยังไม่ได้ทำ Break-even
        # หมายเหตุ: ในระบบจริง ต้องคำนวณกำไรเทียบกับ Risk (Lot * Distance)
        if profit > 0 and not bot_state["is_breakeven"]:
            # Logic การอัปเดต SL บังทุนสำหรับ Multiplier
            update_payload = {
                "contract_update": 1,
                "contract_id": bot_state["contract_id"],
                "limit_order": {"stop_loss": bot_state["entry_price"]}
            }
            await deriv.send(update_payload)
            bot_state["is_breakeven"] = True
            await telegram.send(f"🛡️ <b>Break-even Triggered!</b> SL moved to entry.")

        # ออเดอร์ถูกปิดแล้ว
        if contract.get("is_sold"):
            bot_state["active_trade"] = False
            bot_state["contract_id"] = None
            bot_state["is_breakeven"] = False
            await telegram.send(f"💰 <b>Trade Closed.</b> Profit: {profit}")
            # Update DB state here (Loss streak, Daily profit)

async def trading_loop():
    await deriv.connect()
    
    # Subscribe to 1m and 15m candles
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 60, "style": "candles", "req_id": 1})
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 900, "style": "candles", "req_id": 15})
    
    # Subscribe to Open Contracts stream
    await deriv.send({"proposal_open_contract": 1, "subscribe": 1})

    df_1m = pd.DataFrame()
    df_15m = pd.DataFrame()
    last_processed_time = None

    while True:
        try:
            msg = await deriv.receive()
            
            # จัดการ Active Trades (Break-even & Close)
            await active_trade_manager(msg)

            # ดึงข้อมูลแท่งเทียน
            if "candles" in msg:
                req_id = msg.get("req_id")
                candles = [{"time": c["epoch"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]} for c in msg["candles"]]
                
                if req_id == 1:
                    df_1m = pd.DataFrame(candles)
                elif req_id == 15:
                    df_15m = pd.DataFrame(candles)

            # อัปเดตแท่งเทียนใหม่แบบ Real-time (ohlc stream)
            if "ohlc" in msg:
                ohlc = msg["ohlc"]
                granularity = ohlc.get("granularity")
                
                # เรารอให้ 'แท่งเทียนปิด' เท่านั้นถึงจะคำนวณ (Deriv epoch time เปลี่ยน)
                current_candle_time = ohlc["open_time"]
                
                if granularity == 60 and not df_1m.empty:
                    if current_candle_time != df_1m.iloc[-1]['time']:
                        # แท่งใหม่มาแล้ว แปลว่าแท่งเก่าปิดสมบูรณ์ นำไปคำนวณได้
                        if not bot_state["active_trade"] and not df_15m.empty:
                            
                            # ป้องกัน Trade Deduplication
                            if last_processed_time == df_1m.iloc[-1]['time']:
                                continue
                                
                            signal, sl_dist, tp_dist = strategy.analyze(df_1m, df_15m)
                            
                            if signal:
                                last_processed_time = df_1m.iloc[-1]['time']
                                await telegram.send(f"🚨 <b>SIGNAL: {signal}</b>\nSL: {sl_dist:.4f} | TP: {tp_dist:.4f}")
                                
                                # ⚠️ ตรงนี้คือการยิง API ซื้อขายจริง 
                                # Payload นี้เป็นแค่ตัวอย่างสำหรับ Multiplier Contract
                                buy_payload = {
                                    "buy": 1,
                                    "price": 10, # Stake
                                    "parameters": {
                                        "amount": 10,
                                        "basis": "stake",
                                        "contract_type": "MULTUP" if signal == 'BUY' else "MULTDOWN",
                                        "currency": "USD",
                                        "multiplier": 100,
                                        "symbol": SYMBOL,
                                        "limit_order": {
                                            "stop_loss": sl_dist,
                                            "take_profit": tp_dist
                                        }
                                    }
                                }
                                await deriv.send(buy_payload)
                                bot_state["active_trade"] = True
                                bot_state["is_breakeven"] = False
                                # บอทจะได้รับ contract_id กลับมาใน msg ถัดไป และนำไปเก็บใน bot_state

        except Exception as e:
            await telegram.send(f"❌ <b>Error:</b> {str(e)}")
            await asyncio.sleep(5)