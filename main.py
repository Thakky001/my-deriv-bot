from fastapi import FastAPI
import asyncio
import time
import pandas as pd
import websockets
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

# ค่าเริ่มต้นของ Bot State
bot_state = {
    "active_trade": False, 
    "contract_id": None, 
    "entry_price": 0, 
    "sl": 0, 
    "tp": 0, 
    "is_breakeven": False, 
    "signal_type": "", 
    "last_sell_time": 0
}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram.start_worker())
    
    # 1. โหลดสถานะล่าสุดจาก Database เพื่อป้องกันความจำเสื่อมตอน Restart
    saved_state = await db.get_state()
    if saved_state:
        # อัปเดตค่าในตัวแปร bot_state ด้วยข้อมูลจาก DB
        for key in bot_state:
            if key in saved_state:
                bot_state[key] = saved_state[key]
        await telegram.send("🔄 <b>Bot State Restored</b> from Database.")
    
    asyncio.create_task(trading_loop())

@app.get("/ping")
async def ping(): return {"status": "alive"}

@app.get("/")
async def root(): return {"status": "Deriv Bot API is running!"}

async def update_and_save_state(payload: dict):
    """ อัปเดตค่าใน Memory และบันทึกลง Database พร้อมกัน """
    bot_state.update(payload)
    # ตัด last_sell_time ออกก่อนเซฟลง DB (ถ้าในตารางไม่มีคอลัมน์นี้)
    db_payload = {k: v for k, v in bot_state.items() if k != "last_sell_time"}
    await db.update_state(db_payload)

async def active_trade_manager(msg):
    if "proposal_open_contract" in msg:
        contract = msg["proposal_open_contract"]
        if not contract: return

        c_id = contract.get("contract_id")
        if bot_state["active_trade"] and bot_state["contract_id"] is None:
            await update_and_save_state({"contract_id": c_id})
            
        if bot_state["contract_id"] != c_id: return

        profit = float(contract.get("profit", 0))
        current_spot = float(contract.get("current_spot", 0))
        entry_spot = float(contract.get("entry_spot", 0))
        is_sold = bool(contract.get("is_sold", False))

        if entry_spot and bot_state["entry_price"] == 0:
            await update_and_save_state({"entry_price": entry_spot})

        if is_sold:
            await update_and_save_state({
                "active_trade": False, "contract_id": None, 
                "is_breakeven": False, "entry_price": 0, "sl": 0, "tp": 0
            })
            await telegram.send(f"💰 <b>Trade Closed.</b> Profit: {profit:.2f}")
            return

        # ระบบสำรอง: หาก Server-side SL/TP ทำงานช้า บอทจะช่วยยิงซ้ำ
        if current_spot > 0 and bot_state["sl"] > 0:
            is_buy = bot_state["signal_type"] == 'BUY'
            should_close = False
            if is_buy:
                if current_spot <= bot_state["sl"] or current_spot >= bot_state["tp"]: should_close = True
            else:
                if current_spot >= bot_state["sl"] or current_spot <= bot_state["tp"]: should_close = True

            if should_close:
                current_time = time.time()
                if current_time - bot_state["last_sell_time"] > 10:
                    await deriv.send({"sell": c_id, "price": 0})
                    bot_state["last_sell_time"] = current_time

        # 2. Break-even Logic (เลื่อน SL มากันทุน)
        if not bot_state["is_breakeven"] and bot_state["entry_price"] > 0:
            diff_to_sl = abs(bot_state["entry_price"] - bot_state["sl"])
            if diff_to_sl > 0:
                is_buy = bot_state["signal_type"] == 'BUY'
                triggered = (is_buy and current_spot >= bot_state["entry_price"] + diff_to_sl) or \
                            (not is_buy and current_spot <= bot_state["entry_price"] - diff_to_sl)
                
                if triggered:
                    await update_and_save_state({"sl": bot_state["entry_price"], "is_breakeven": True})
                    # ส่งคำสั่งอัปเดต SL ไปยังเซิร์ฟเวอร์ Deriv ด้วย
                    await deriv.send({
                        "set_self_exclusion": 1, # หรือใช้ set_settings ตามประเภทสัญญา
                        "contract_id": c_id,
                        "limit_order": {"stop_loss": 0.01} # ตั้งให้ตัดที่ทุน
                    })
                    await telegram.send("🛡️ <b>Break-even Triggered!</b> State Saved.")

async def trading_loop():
    df_1m = pd.DataFrame()
    df_15m = pd.DataFrame()
    last_processed_time = None

    while True: # 🔁 ลูปชั้นนอก: Reconnect
        try:
            await deriv.connect()
            await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 60, "style": "candles", "req_id": 1, "subscribe": 1})
            await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 900, "style": "candles", "req_id": 15, "subscribe": 1})
            await deriv.send({"proposal_open_contract": 1, "subscribe": 1})

            if bot_state["contract_id"]:
                await deriv.send({"proposal_open_contract": 1, "contract_id": bot_state["contract_id"], "subscribe": 1})

            while True: # 🔁 ลูปชั้นใน: Processing
                msg = await deriv.receive()
                if "error" in msg:
                    await telegram.send(f"⚠️ <b>API Error:</b> {msg['error'].get('message')}")
                
                if "buy" in msg:
                    await update_and_save_state({"contract_id": msg["buy"].get("contract_id"), "active_trade": True})
                    await telegram.send(f"✅ <b>Order Placed!</b> ID: {bot_state['contract_id']}")
                
                await active_trade_manager(msg)

                if "candles" in msg:
                    candles = [{"time": c["epoch"], "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in msg["candles"]]
                    if msg.get("req_id") == 1: df_1m = pd.DataFrame(candles)
                    else: df_15m = pd.DataFrame(candles)

                if "ohlc" in msg:
                    ohlc = msg["ohlc"]
                    granularity = ohlc.get("granularity")
                    open_time = ohlc["open_time"]
                    df_target = df_1m if granularity == 60 else df_15m
                    
                    if not df_target.empty:
                        last_time = df_target.iloc[-1]['time']
                        if open_time == last_time:
                            for k in ['close', 'high', 'low']: df_target.at[df_target.index[-1], k] = float(ohlc[k])
                        elif open_time > last_time:
                            if granularity == 60 and not bot_state["active_trade"] and not df_15m.empty:
                                if last_processed_time != last_time:
                                    signal, sl_dist, tp_dist = strategy.analyze(df_1m, df_15m)
                                    if signal:
                                        last_processed_time = last_time
                                        curr = df_target.iloc[-1]['close']
                                        sl_price = curr - sl_dist if signal == 'BUY' else curr + sl_dist
                                        tp_price = curr + tp_dist if signal == 'BUY' else curr - tp_dist
                                        
                                        # คำนวณเป็นจำนวนเงิน (Amount) สำหรับ limit_order ของ Multiplier
                                        stake = 10
                                        mult = 100
                                        sl_amount = round((sl_dist / curr) * mult * stake, 2)
                                        tp_amount = round((tp_dist / curr) * mult * stake, 2)

                                        await update_and_save_state({
                                            "signal_type": signal, "sl": sl_price, "tp": tp_price, 
                                            "active_trade": True, "is_breakeven": False, "entry_price": 0
                                        })
                                        
                                        await telegram.send(f"🚨 <b>SIGNAL: {signal}</b>\nSL: {sl_price:.4f} | TP: {tp_price:.4f}")
                                        
                                        # 3. ส่งคำสั่งซื้อพร้อม SL/TP ไปยังเซิร์ฟเวอร์ทันที
                                        await deriv.send({
                                            "buy": 1, "price": stake,
                                            "parameters": {
                                                "amount": stake, "basis": "stake", "symbol": SYMBOL, "currency": "USD", "multiplier": mult,
                                                "contract_type": "MULTUP" if signal == 'BUY' else "MULTDOWN",
                                                "limit_order": {
                                                    "stop_loss": sl_amount,
                                                    "take_profit": tp_amount
                                                }
                                            }
                                        })
                            
                            new_df = pd.DataFrame([{"time": open_time, "open": float(ohlc['open']), "high": float(ohlc['high']), "low": float(ohlc['low']), "close": float(ohlc['close'])}])
                            if granularity == 60: df_1m = pd.concat([df_1m, new_df], ignore_index=True).iloc[-300:]
                            else: df_15m = pd.concat([df_15m, new_df], ignore_index=True).iloc[-300:]

        except Exception as e:
            await telegram.send(f"❌ <b>Connection Error:</b> {str(e)}. Reconnecting...")
            await asyncio.sleep(5)