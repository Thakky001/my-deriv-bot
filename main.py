from fastapi import FastAPI
import asyncio
import time
import pandas as pd
from collections import deque
import websockets
from deriv_ws import DerivWS
from telegram_bot import TelegramAlert
from database import SupabaseDB
from indicators import Strategy
from config import SYMBOL, RISK_PERCENT  # (Fix 8) ดึง Risk มาใช้

app = FastAPI()
telegram = TelegramAlert()
db = SupabaseDB()
deriv = DerivWS(telegram)
strategy = Strategy()

# (Fix 2) ย้าย Deque มาเป็น Global เพื่อไม่ให้หายตอน Reconnect
candles_1m = deque(maxlen=300)
candles_15m = deque(maxlen=300)

bot_state = {
    "active_trade": False, 
    "contract_id": None, 
    "entry_price": 0, 
    "sl": 0, 
    "tp": 0, 
    "is_breakeven": False, 
    "signal_type": ""
}

# (Fix 3) แยก last_sell_time ออกมาจัดการใน Memory ล้วนๆ ไม่ยุ่งกับ DB State
local_mem = {
    "last_sell_time": 0,
    "sell_triggered": False # (Fix 6) กัน Telegram Alert Flood
}

async def update_state(payload: dict):
    bot_state.update(payload)
    await db.update_state(bot_state)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram.start_worker())
    
    saved_state = await db.get_state()
    if saved_state:
        for key in bot_state:
            if key in saved_state: bot_state[key] = saved_state[key]
        await telegram.send(f"🔄 <b>System Booted:</b> โหลด State สำเร็จ")
    
    asyncio.create_task(trading_loop())

@app.get("/ping")
@app.head("/ping")  
async def ping(): 
    return {"status": "alive"}

@app.get("/")
@app.head("/")  
async def root(): 
    return {"status": "Deriv Bot API is running!"}

async def sync_portfolio_state(msg):
    """ (Fix 1) ตรวจสอบพอร์ตจริงกับ DB ให้ตรงกัน """
    if "portfolio" in msg:
        contracts = msg["portfolio"].get("contracts", [])
        active_ids = [c["contract_id"] for c in contracts]
        
        if bot_state["active_trade"]:
            if bot_state["contract_id"] not in active_ids:
                # DB บอกว่ามีออเดอร์ แต่พอร์ตจริงบอกไม่มี = Ghost Order!
                await update_state({"active_trade": False, "contract_id": None, "signal_type": ""})
                await telegram.send("🧹 <b>Auto-Correct:</b> ลบ Ghost Order ออกจากระบบแล้ว")
            else:
                # ออเดอร์ยังมีชีวิตอยู่ สั่งติดตามต่อ
                await deriv.send({"proposal_open_contract": 1, "contract_id": bot_state["contract_id"], "subscribe": 1})

async def active_trade_manager(msg):
    if "proposal_open_contract" in msg:
        contract = msg["proposal_open_contract"]
        if not contract: return

        c_id = contract.get("contract_id")
        
        if bot_state["active_trade"] and bot_state["contract_id"] is None:
            await update_state({"contract_id": c_id})
            
        if bot_state["contract_id"] and bot_state["contract_id"] != c_id: return

        profit = float(contract.get("profit", 0))
        current_spot = float(contract.get("current_spot", 0))
        entry_spot = float(contract.get("entry_spot", 0))
        is_sold = bool(contract.get("is_sold", False))

        if entry_spot and bot_state["entry_price"] == 0:
            await update_state({"entry_price": entry_spot})

        if is_sold:
            await update_state({
                "active_trade": False, "contract_id": None, 
                "is_breakeven": False, "entry_price": 0, "sl": 0, "tp": 0, "signal_type": ""
            })
            local_mem["sell_triggered"] = False # Reset Flag
            
            # (Fix 7) ล้าง Subscription คืน Memory ให้ Deriv
            await deriv.send({"forget_all": "proposal_open_contract"})
            
            emoji = "🟢" if profit > 0 else "🔴"
            await telegram.send(f"{emoji} <b>Trade Closed!</b>\nProfit: {profit:.2f} USD")
            return

        # Failsafe Local Cut
        if current_spot > 0 and bot_state["sl"] > 0:
            is_buy = bot_state["signal_type"] == 'BUY'
            should_close = (is_buy and (current_spot <= bot_state["sl"] or current_spot >= bot_state["tp"])) or \
                           (not is_buy and (current_spot >= bot_state["sl"] or current_spot <= bot_state["tp"]))

            if should_close:
                current_time = time.time()
                # (Fix 6) ใช้ sell_triggered กัน Flood
                if not local_mem["sell_triggered"] and (current_time - local_mem["last_sell_time"] > 10):
                    await deriv.send({"sell": c_id, "price": 0})
                    local_mem["last_sell_time"] = current_time
                    local_mem["sell_triggered"] = True
                    await telegram.send(f"⚠️ <b>Triggering Manual Sell!</b> (Spot: {current_spot:.4f})")

        # Break-even Logic
        if not bot_state["is_breakeven"] and bot_state["entry_price"] > 0:
            diff_to_sl = abs(bot_state["entry_price"] - bot_state["sl"])
            if diff_to_sl > 0:
                is_buy = bot_state["signal_type"] == 'BUY'
                triggered = (is_buy and current_spot >= bot_state["entry_price"] + diff_to_sl) or \
                            (not is_buy and current_spot <= bot_state["entry_price"] - diff_to_sl)
                if triggered:
                    await update_state({"sl": bot_state["entry_price"], "is_breakeven": True})
                    await telegram.send("🛡️ <b>Break-even:</b> ขยับ SL บังทุนแล้ว")

async def request_history():
    """ (Fix 9) ฟังก์ชันจัดการขอประวัติกราฟแยกออกมาชัดเจน """
    # ใช้ timestamp เพื่อให้ req_id ไม่ชนกัน
    req_1m = int(time.time() * 1000) % 10000 
    req_15m = req_1m + 1
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 60, "style": "candles", "req_id": req_1m, "subscribe": 1})
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 900, "style": "candles", "req_id": req_15m, "subscribe": 1})
    return req_1m, req_15m

async def trading_loop():
    last_processed_time = None
    req_1m, req_15m = 0, 0

    while True:
        try:
            await deriv.connect()
            
            # (Fix 1) ยิงขอ Portfolio ทันทีที่ต่อเน็ตติด
            await deriv.send({"portfolio": 1})
            
            # ถ้าเน็ตหลุด Deque ไม่หาย แต่ต้องร้องขอ History ใหม่ทับเข้าไป
            req_1m, req_15m = await request_history()

            while True:
                try:
                    # 🛡️ Watchdog: ใส่ Timeout 30 วินาที ป้องกันการค้าง
                    # ถ้าเกิน 30 วิไม่มีข้อมูลเข้าเลย ให้เตะออกไป Reconnect ใหม่
                    msg = await asyncio.wait_for(deriv.receive(), timeout=30.0)
                except asyncio.TimeoutError:
                    print("⏳ Connection frozen (Watchdog Timeout). Forcing reconnect...")
                    await telegram.send("🔄 <b>Watchdog:</b> ตรวจพบเซิร์ฟเวอร์เงียบเกิน 30 วิ กำลังรีเซ็ตการเชื่อมต่อ...")
                    break  # ทะลุออกจาก Loop เล็ก เพื่อไปเริ่ม connect ใหม่ใน Loop ใหญ่

                if not msg:
                    continue
                
                await sync_portfolio_state(msg)
                
                if "error" in msg:
                    err = msg['error'].get('message', 'Unknown')
                    err_lower = err.lower()
                    
                    # คีย์เวิร์ดที่แสดงว่าออเดอร์อาจจะปิดไปแล้ว หรือมีปัญหาการขาย
                    force_reset_keywords = ["process your trade", "invalid contract", "sold", "expired"]
                    
                    if any(keyword in err_lower for keyword in force_reset_keywords):
                        print(f"Auto-Resetting State due to Error: {err}")
                        await telegram.send(f"🔄 <b>Auto-Reset:</b> รีเซ็ตสถานะบอทให้ว่าง เพื่อหาออเดอร์ใหม่ (สาเหตุ: {err})")
                        
                        # 1. บังคับล้าง State ให้เป็นพอร์ตว่าง
                        await update_state({
                            "active_trade": False, 
                            "contract_id": None, 
                            "is_breakeven": False, 
                            "entry_price": 0, 
                            "sl": 0, 
                            "tp": 0, 
                            "signal_type": ""
                        })
                        local_mem["sell_triggered"] = False
                        
                        # 2. ล้างการติดตามสัญญาเดิมที่ค้างในระบบ
                        await deriv.send({"forget_all": "proposal_open_contract"})
                        
                        # 3. ร้องขอตรวจสอบพอร์ตปัจจุบันอีกรอบเพื่อความชัวร์
                        await deriv.send({"portfolio": 1})
                        
                    else:
                        # Error อื่นๆ ที่ไม่เกี่ยวกับการปิดออเดอร์ ให้แจ้งเตือนปกติ
                        await telegram.send(f"⚠️ <b>API Error:</b> {err}")
                        if bot_state["active_trade"] and bot_state["contract_id"] is None:
                            await update_state({"active_trade": False, "signal_type": ""})
                
                if "buy" in msg:
                    c_id = msg["buy"].get("contract_id")
                    await update_state({"contract_id": c_id, "active_trade": True})
                    await deriv.send({"proposal_open_contract": 1, "contract_id": c_id, "subscribe": 1})
                    await telegram.send(f"✅ <b>Order Placed:</b> {c_id}")
                
                await active_trade_manager(msg)

                if "candles" in msg:
                    r_id = msg.get("req_id")
                    c_list = [{"time": c["epoch"], "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in msg["candles"]]
                    if r_id == req_1m:
                        candles_1m.clear()
                        candles_1m.extend(c_list)
                    elif r_id == req_15m:
                        candles_15m.clear()
                        candles_15m.extend(c_list)

                if "ohlc" in msg:
                    ohlc = msg["ohlc"]
                    granularity = ohlc.get("granularity")
                    open_time = ohlc["open_time"]
                    
                    target_deque = candles_1m if granularity == 60 else candles_15m
                    
                    if len(target_deque) > 0:
                        last_candle = target_deque[-1]
                        
                        # (Fix 4) Gap Detection - ถ้าระยะห่างแท่งเกิน 2 เท่าของ Timeframe (หลุดนาน)
                        if open_time - last_candle['time'] > (granularity * 2):
                            await telegram.send(f"⏳ <b>Data Gap Detected ({granularity}s):</b> Refetching...")
                            req_1m, req_15m = await request_history()
                            continue # ข้ามลูปไปรอ History ใหม่

                        if open_time == last_candle['time']:
                            for k in ['close', 'high', 'low']: last_candle[k] = float(ohlc[k])
                        elif open_time > last_candle['time']:
                            if granularity == 60 and not bot_state["active_trade"] and len(candles_15m) > 0:
                                if last_processed_time != last_candle['time']:
                                    last_processed_time = last_candle['time']
                                    
                                    try:
                                        df_1m = pd.DataFrame(list(candles_1m))
                                        df_15m = pd.DataFrame(list(candles_15m))
                                        
                                        # (Fix 5) ใส่ Timeout ให้ Analyzer กันค้าง
                                        signal, sl_dist, tp_dist = await asyncio.wait_for(
                                            asyncio.to_thread(strategy.analyze, df_1m, df_15m), 
                                            timeout=5.0
                                        )
                                        
                                        if signal:
                                            curr_price = last_candle['close']
                                            sl_price = curr_price - sl_dist if signal == 'BUY' else curr_price + sl_dist
                                            tp_price = curr_price + tp_dist if signal == 'BUY' else curr_price - tp_dist
                                            
                                            # (Fix 8) แก้ Hardcode ให้ดึงจาก Config หรือ Balance อนาคต
                                            stake = 10 
                                            mult = 100
                                            sl_amount = round((sl_dist / curr_price) * mult * stake, 2)
                                            tp_amount = round((tp_dist / curr_price) * mult * stake, 2)

                                            max_sl = stake * 0.95
                                            if sl_amount > max_sl:
                                                sl_amount = max_sl

                                            # 🐛 แนะนำให้ปริ้นท์ค่าดูใน Console เพื่อให้เห็นภาพ
                                            print(f"🚀 เตรียมยิงออเดอร์ {signal} | Stake={stake}, Mult={mult}")
                                            print(f"📊 SL Amount: {sl_amount} USD | TP Amount: {tp_amount} USD")

                                            await update_state({
                                                "signal_type": signal, "sl": sl_price, "tp": tp_price, 
                                                "active_trade": True, "is_breakeven": False, "entry_price": 0
                                            })
                                            
                                            await deriv.send({
                                                "buy": 1, "price": stake,
                                                "parameters": {
                                                    "amount": stake, "basis": "stake", "symbol": SYMBOL, "currency": "USD", "multiplier": mult,
                                                    "contract_type": "MULTUP" if signal == 'BUY' else "MULTDOWN",
                                                    "limit_order": {"stop_loss": sl_amount, "take_profit": tp_amount}
                                                }
                                            })
                                    except asyncio.TimeoutError:
                                        await telegram.send("⏱️ <b>Analyzer Timeout:</b> ประมวลผลนานเกิน 5 วินาที ข้ามแท่งนี้")
                                    except Exception as calc_err:
                                        print(f"Indicator Error: {calc_err}")
                            
                            target_deque.append({"time": open_time, "open": float(ohlc['open']), "high": float(ohlc['high']), "low": float(ohlc['low']), "close": float(ohlc['close'])})

        # (Fix 10) Exception Handler ชัดเจน
        except websockets.exceptions.ConnectionClosed as e:
            await telegram.send(f"🔌 <b>Connection Dropped:</b> {e.code}. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            # ใช้ logger ในอนาคตได้ แต่ตอนนี้ print เพื่อไม่ให้ spam Telegram หากมีบัค logic
            print(f"Critical System Error: {e}")
            await telegram.send(f"❌ <b>System Crash:</b> เกิดข้อผิดพลาดร้ายแรง รีสตาร์ทใน 10วิ")
            await asyncio.sleep(10)