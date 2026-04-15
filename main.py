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

bot_state = {"active_trade": False, "contract_id": None, "entry_price": 0, "sl": 0, "tp": 0, "is_breakeven": False, "signal_type": "", "last_sell_time": 0}

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram.start_worker())
    asyncio.create_task(trading_loop())

@app.get("/ping")
@app.head("/ping")
async def ping():
    return {"status": "alive"}

@app.get("/")
@app.head("/")
async def root():
    return {"status": "Deriv Bot API is running!"}

async def active_trade_manager(msg):
    """ จัดการออเดอร์, เช็คการชน SL/TP เบื้องหลัง และสั่ง Sell """
    if "proposal_open_contract" in msg:
        contract = msg["proposal_open_contract"]
        if not contract:
            return

        c_id = contract.get("contract_id")
        
        # ถ้ายังไม่มีรหัส contract (อาจเป็นเพราะ buy response มาทีหลังสตรีมนี้)
        if bot_state["active_trade"] and bot_state["contract_id"] is None:
            bot_state["contract_id"] = c_id
            
        # ถ้าไม่ได้รัน contract นี้ข้ามไป
        if bot_state["contract_id"] and bot_state["contract_id"] != c_id:
            return

        profit = float(contract.get("profit", 0))
        current_spot = float(contract.get("current_spot", 0))
        entry_spot = float(contract.get("entry_spot", 0))
        is_sold = bool(contract.get("is_sold", False))

        # อัปเดต entry_price ล่าสุด
        if entry_spot and bot_state["entry_price"] == 0:
            bot_state["entry_price"] = entry_spot

        if is_sold:
            bot_state["active_trade"] = False
            bot_state["contract_id"] = None
            bot_state["is_breakeven"] = False
            bot_state["entry_price"] = 0
            await telegram.send(f"💰 <b>Trade Closed.</b> Profit: {profit:.2f}")
            return

        if not current_spot or bot_state["entry_price"] == 0 or bot_state["sl"] == 0 or bot_state["tp"] == 0:
            return

        # คำนวณสัญญาณว่าเป็น BUY หรือ SELL
        is_buy = bot_state["signal_type"] == 'BUY'
        
        should_close = False
        close_reason = ""

        # 1. เช็คเป้าหมาย SL / TP
        if is_buy:
            if current_spot <= bot_state["sl"]:
                should_close, close_reason = True, "Stop Loss"
            elif current_spot >= bot_state["tp"]:
                should_close, close_reason = True, "Take Profit"
        else:
            if current_spot >= bot_state["sl"]:
                should_close, close_reason = True, "Stop Loss"
            elif current_spot <= bot_state["tp"]:
                should_close, close_reason = True, "Take Profit"

        if should_close:
            current_time = time.time()
            last_sell = bot_state.get("last_sell_time", 0)
            if current_time - last_sell > 5:  # หน่วงเวลา 5 วินาทีหากยิงไม่ผ่าน
                await telegram.send(f"⚠️ <b>Triggering {close_reason}</b> manually! (Spot: {current_spot:.4f})")
                sell_payload = {"sell": bot_state["contract_id"], "price": 0}
                await deriv.send(sell_payload)
                bot_state["last_sell_time"] = current_time
            return

        # 2. เช็ค Break-even กันทุนถ้าราคาวิ่งไปถึง Risk/Reward 1:1
        if not bot_state["is_breakeven"]:
            diff_to_sl = abs(bot_state["entry_price"] - bot_state["sl"])
            if diff_to_sl > 0:
                if is_buy and current_spot >= bot_state["entry_price"] + diff_to_sl:
                    bot_state["sl"] = bot_state["entry_price"]
                    bot_state["is_breakeven"] = True
                    await telegram.send("🛡️ <b>Break-even Triggered!</b> SL moved to entry.")
                elif not is_buy and current_spot <= bot_state["entry_price"] - diff_to_sl:
                    bot_state["sl"] = bot_state["entry_price"]
                    bot_state["is_breakeven"] = True
                    await telegram.send("🛡️ <b>Break-even Triggered!</b> SL moved to entry.")

async def trading_loop():
    df_1m = pd.DataFrame()
    df_15m = pd.DataFrame()
    last_processed_time = None

    while True:  # 🔁 ลูปชั้นนอก: ทำหน้าที่ Reconnect
        try:
            await deriv.connect()
            
            # Subscribe ใหม่ทุกครั้งที่เชื่อมต่อสำเร็จ
            await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 60, "style": "candles", "req_id": 1, "subscribe": 1})
            await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 900, "style": "candles", "req_id": 15, "subscribe": 1})
            await deriv.send({"proposal_open_contract": 1, "subscribe": 1})

            # 🛡️ ถ้าเน็ตหลุดตอนมีออเดอร์ค้างอยู่ ให้ Subscribe ติดตามออเดอร์เดิมต่อด้วย
            if bot_state.get("contract_id"):
                await deriv.send({"proposal_open_contract": 1, "contract_id": bot_state["contract_id"], "subscribe": 1})

            while True:  # 🔁 ลูปชั้นใน: รับข้อมูลและเทรดไปเรื่อยๆ
                msg = await deriv.receive()
                
                # ดักจับผลลัพธ์จาก API หากเกิด Error หรือตอบกลับสถานะ
                if "error" in msg:
                    await telegram.send(f"⚠️ <b>Deriv API Error:</b> {msg['error'].get('message', 'Unknown Error')}")
                    if bot_state["active_trade"] and bot_state["contract_id"] is None:
                        bot_state["active_trade"] = False
                        bot_state["signal_type"] = ""
                
                if "buy" in msg:
                    buy_data = msg["buy"]
                    c_id = buy_data.get("contract_id")
                    bot_state["contract_id"] = c_id
                    if c_id:
                        await deriv.send({"proposal_open_contract": 1, "contract_id": c_id, "subscribe": 1})
                    await telegram.send(f"✅ <b>Order Placed!</b> Contract ID: {c_id}")
                
                # จัดการ Active Trades (Break-even & Close)
                await active_trade_manager(msg)

                # ดึงข้อมูลแท่งเทียน
                if "candles" in msg:
                    req_id = msg.get("req_id")
                    candles = [{"time": c["epoch"], "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in msg["candles"]]
                    
                    if req_id == 1:
                        df_1m = pd.DataFrame(candles)
                    elif req_id == 15:
                        df_15m = pd.DataFrame(candles)

                # อัปเดตแท่งเทียนใหม่แบบ Real-time (ohlc stream)
                if "ohlc" in msg:
                    ohlc = msg["ohlc"]
                    granularity = ohlc.get("granularity")
                    open_time = ohlc["open_time"]
                    
                    df_target = df_1m if granularity == 60 else df_15m
                    
                    if not df_target.empty:
                        last_time = df_target.iloc[-1]['time']
                        
                        if open_time == last_time:
                            df_target.at[df_target.index[-1], 'close'] = float(ohlc['close'])
                            df_target.at[df_target.index[-1], 'high'] = float(ohlc['high'])
                            df_target.at[df_target.index[-1], 'low'] = float(ohlc['low'])
                        elif open_time > last_time:
                            if granularity == 60 and not bot_state["active_trade"] and not df_15m.empty:
                                if last_processed_time != last_time:
                                    signal, sl_dist, tp_dist = strategy.analyze(df_1m, df_15m)
                                    
                                    if signal:
                                        last_processed_time = last_time
                                        current_price = df_target.iloc[-1]['close']
                                        
                                        bot_state["signal_type"] = signal
                                        if signal == 'BUY':
                                            bot_state["sl"] = current_price - sl_dist
                                            bot_state["tp"] = current_price + tp_dist
                                        else:
                                            bot_state["sl"] = current_price + sl_dist
                                            bot_state["tp"] = current_price - tp_dist
                                            
                                        await telegram.send(f"🚨 <b>SIGNAL: {signal}</b>\nSL: {bot_state['sl']:.4f} | TP: {bot_state['tp']:.4f}")
                                        
                                        buy_payload = {
                                            "buy": 1,
                                            "price": 10, 
                                            "parameters": {
                                                "amount": 10,
                                                "basis": "stake",
                                                "contract_type": "MULTUP" if signal == 'BUY' else "MULTDOWN",
                                                "currency": "USD",
                                                "multiplier": 100,
                                                "symbol": SYMBOL
                                            }
                                        }
                                        await deriv.send(buy_payload)
                                        bot_state["active_trade"] = True
                                        bot_state["contract_id"] = None
                                        bot_state["entry_price"] = 0
                                        bot_state["is_breakeven"] = False
                            
                            new_row = {"time": open_time, "open": float(ohlc['open']), "high": float(ohlc['high']), "low": float(ohlc['low']), "close": float(ohlc['close'])}
                            new_df = pd.DataFrame([new_row])
                            if granularity == 60:
                                df_1m = pd.concat([df_1m, new_df], ignore_index=True).iloc[-300:]
                            else:
                                df_15m = pd.concat([df_15m, new_df], ignore_index=True).iloc[-300:]

        except websockets.exceptions.ConnectionClosed as e:
            # ดักจับเคสเน็ตหลุด / Websocket ปิดโดยเฉพาะ
            await telegram.send(f"⚠️ <b>Connection Lost:</b> {str(e)}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            # ดักจับ Error อื่นๆ
            await telegram.send(f"❌ <b>System Error:</b> {str(e)}. Retrying in 5s...")
            await asyncio.sleep(5)