from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import asyncio
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
from collections import deque
import websockets
import itertools
from deriv_ws import DerivWS
from telegram_bot import TelegramAlert
from database import SupabaseDB
from indicators import Strategy
from config import SYMBOL, RISK_PERCENT

app = FastAPI()
telegram = TelegramAlert()
db = SupabaseDB()
deriv = DerivWS(telegram)
strategy = Strategy()

# Deque สำหรับเก็บข้อมูลกราฟ
candles_1m = deque(maxlen=300)
candles_15m = deque(maxlen=300)

TH_TZ = timezone(timedelta(hours=7))

# [Fix 2] ใช้ itertools.count() การันตี Atomic & Thread-safe หมดปัญหา req_id ชนกันตอน reconnect ซ้อน
global_req_counter = itertools.count(1)

bot_state = {
    "active_trade": False, 
    "contract_id": None, 
    "entry_price": 0, 
    "sl": 0, 
    "tp": 0, 
    "is_breakeven": False, 
    "signal_type": "",
    "total_profit": 0.0,
    "win_count": 0,
    "loss_count": 0
}

# จัดการข้อมูลชั่วคราวใน Memory
local_mem = {
    "last_sell_time": 0,
    "sell_triggered": False,
    "is_processing_close": False,
    "last_heartbeat": 0,
    "last_1m_candle_time": time.time()
}

# [Fix 8] ระบบ Cache สำหรับ Dashboard ป้องกัน API Quota Limit
dashboard_cache = {
    "data": {},
    "last_updated": 0
}

async def update_state(payload: dict):
    bot_state.update(payload)
    await db.update_state(bot_state)

@app.on_event("startup")
async def startup_event():
    telegram.worker_task = asyncio.create_task(telegram.start_worker())
    
    saved_state = await db.get_state()
    if saved_state:
        for key in bot_state:
            if key in saved_state: bot_state[key] = saved_state[key]
        await telegram.send(f"🔄 <b>System Booted:</b> โหลด State สำเร็จ")
    
    asyncio.create_task(trading_loop())

@app.on_event("shutdown")
async def shutdown_event():
    print("🛑 กำลังเข้าสู่กระบวนการปิดระบบอย่างปลอดภัย (Graceful Shutdown)...")
    await telegram.send("🛑 <b>System:</b> เซิร์ฟเวอร์กำลังถูกปิดการทำงาน...")
    await asyncio.sleep(2) # รอให้ข้อความสุดท้ายส่งออกไป
    
    await db.close()
    await telegram.close()
    
    if deriv.ws:
        await deriv.ws.close()
        print("🔒 [Deriv WS] Connection closed.")
    print("✅ ปิดระบบสมบูรณ์")

@app.get("/ping")
@app.head("/ping")  
async def ping(): 
    return {"status": "alive"}

@app.get("/", response_class=HTMLResponse)
async def root(): 
    """ หน้า Dashboard เช็คผลประกอบการรายวันและรายเดือน (มีระบบ Tab) """
    # [Fix 8] โหลดข้อมูลใหม่เฉพาะตอนเริ่มระบบ หรือเมื่อ Cache หมดอายุ (5 นาที)
    current_time = time.time()
    if dashboard_cache["last_updated"] == 0 or (current_time - dashboard_cache["last_updated"] > 300):
        fetched_data = await db.get_all_daily_history()
        if fetched_data is not None:
            dashboard_cache["data"] = fetched_data
            dashboard_cache["last_updated"] = current_time

    wins = bot_state.get("win_count", 0)
    losses = bot_state.get("loss_count", 0)
    total_trades = wins + losses
    profit = bot_state.get("total_profit", 0.0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    daily_stats = dashboard_cache["data"]
    monthly_stats = {}
    daily_rows_html = ""
    monthly_rows_html = ""

    sorted_dates = sorted(daily_stats.keys(), reverse=True)

    for date_str in sorted_dates:
        day_data = daily_stats[date_str]
        month_str = date_str[:7] 
        
        if month_str not in monthly_stats:
            monthly_stats[month_str] = {"profit": 0.0, "wins": 0, "losses": 0}
        
        monthly_stats[month_str]["profit"] += day_data["profit"]
        monthly_stats[month_str]["wins"] += day_data["wins"]
        monthly_stats[month_str]["losses"] += day_data["losses"]

        d_trades = day_data["wins"] + day_data["losses"]
        d_rate = (day_data["wins"] / d_trades * 100) if d_trades > 0 else 0
        d_color = "#2ecc71" if day_data["profit"] >= 0 else "#e74c3c"
        daily_rows_html += f"<tr><td>{date_str}</td><td>{d_trades}</td><td>{d_rate:.1f}%</td><td style='color:{d_color}; font-weight:bold;'>{day_data['profit']:.2f}</td></tr>"

    for m_str, m_data in monthly_stats.items():
        m_trades = m_data["wins"] + m_data["losses"]
        m_rate = (m_data["wins"] / m_trades * 100) if m_trades > 0 else 0
        m_color = "#2ecc71" if m_data["profit"] >= 0 else "#e74c3c"
        monthly_rows_html += f"<tr><td>{m_str}</td><td>{m_trades}</td><td>{m_rate:.1f}%</td><td style='color:{m_color}; font-weight:bold;'>{m_data['profit']:.2f}</td></tr>"

    if not daily_rows_html:
        daily_rows_html = "<tr><td colspan='4' style='text-align:center;'>ยังไม่มีข้อมูล</td></tr>"
    if not monthly_rows_html:
        monthly_rows_html = "<tr><td colspan='4' style='text-align:center;'>ยังไม่มีข้อมูล</td></tr>"

    html_content = f"""
    <html>
        <head>
            <title>Deriv Bot Dashboard</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    background-color: #f0f2f5; 
                    color: #333; 
                    margin: 0; 
                    padding: 20px; 
                    display: flex; 
                    flex-direction: column; 
                    align-items: center; 
                }}
                .container {{ width: 100%; max-width: 800px; }}
                .header-title {{ text-align: center; color: #1c1e21; margin-bottom: 20px; }}
                .tabs {{ 
                    display: flex; 
                    justify-content: center; 
                    gap: 10px; 
                    margin-bottom: 20px; 
                    background: white;
                    padding: 10px;
                    border-radius: 12px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.05);
                }}
                .tab-btn {{
                    background: none;
                    border: none;
                    padding: 10px 20px;
                    font-size: 16px;
                    font-weight: bold;
                    color: #666;
                    cursor: pointer;
                    border-radius: 8px;
                    transition: all 0.3s ease;
                }}
                .tab-btn:hover {{ background: #f0f2f5; color: #333; }}
                .tab-btn.active {{ background: #007bff; color: white; }}
                .tab-content {{ display: none; animation: fadeIn 0.4s; }}
                .card {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
                @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
                h2, h3 {{ color: #1c1e21; margin-top: 0; text-align: center; border-bottom: 2px solid #f0f2f5; padding-bottom: 10px; }}
                .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-top: 15px; }}
                .stat-box {{ background: #f8f9fa; padding: 20px 15px; border-radius: 8px; text-align: center; border-bottom: 3px solid #007bff; }}
                .stat-label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 1px; }}
                .stat-value {{ font-size: 28px; font-weight: bold; margin-top: 8px; }}
                .profit {{ color: {'#2ecc71' if profit >= 0 else '#e74c3c'}; }}
                .table-container {{ overflow-x: auto; }}
                table {{ width: 100%; border-collapse: collapse; font-size: 14px; text-align: right; margin-top: 15px; }}
                th, td {{ padding: 12px 15px; border-bottom: 1px solid #eee; }}
                th {{ text-align: right; color: #666; font-weight: bold; font-size: 13px; text-transform: uppercase; background: #f8f9fa; }}
                th:first-child, td:first-child {{ text-align: left; }}
                tr:hover {{ background-color: #f8f9fa; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1 class="header-title">📈 Deriv Bot Dashboard</h1>
                <div class="tabs">
                    <button class="tab-btn active" onclick="openTab(event, 'Overview')">ภาพรวม (Overview)</button>
                    <button class="tab-btn" onclick="openTab(event, 'Monthly')">รายเดือน (Monthly)</button>
                    <button class="tab-btn" onclick="openTab(event, 'Daily')">รายวัน (Daily)</button>
                </div>
                <div id="Overview" class="tab-content" style="display: block;">
                    <div class="card">
                        <h2>🤖 ภาพรวมพอร์ต (All-Time)</h2>
                        <div class="stat-grid">
                            <div class="stat-box">
                                <div class="stat-label">กำไรรวม (USD)</div>
                                <div class="stat-value profit">{profit:.2f}</div>
                            </div>
                            <div class="stat-box">
                                <div class="stat-label">อัตราชนะ (Win Rate)</div>
                                <div class="stat-value">{win_rate:.1f}%</div>
                            </div>
                            <div class="stat-box">
                                <div class="stat-label">เทรดทั้งหมด</div>
                                <div class="stat-value">{total_trades}</div>
                            </div>
                            <div class="stat-box">
                                <div class="stat-label">ชนะ / แพ้</div>
                                <div class="stat-value"><span style="color:#2ecc71">{wins}</span> / <span style="color:#e74c3c">{losses}</span></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div id="Monthly" class="tab-content">
                    <div class="card">
                        <h3>📅 สรุปรายเดือน (Monthly)</h3>
                        <div class="table-container">
                            <table>
                                <tr><th>เดือน</th><th>จำนวนเทรด</th><th>Win Rate</th><th>กำไร</th></tr>
                                {monthly_rows_html}
                            </table>
                        </div>
                    </div>
                </div>
                <div id="Daily" class="tab-content">
                    <div class="card">
                        <h3>📝 สถิติรายวัน (Daily)</h3>
                        <div class="table-container">
                            <table>
                                <tr><th>วันที่</th><th>จำนวนเทรด</th><th>Win Rate</th><th>กำไร</th></tr>
                                {daily_rows_html}
                            </table>
                        </div>
                    </div>
                </div>
            </div>
            <script>
                function openTab(evt, tabName) {{
                    var i, tabcontent, tablinks;
                    tabcontent = document.getElementsByClassName("tab-content");
                    for (i = 0; i < tabcontent.length; i++) {{
                        tabcontent[i].style.display = "none";
                    }}
                    tablinks = document.getElementsByClassName("tab-btn");
                    for (i = 0; i < tablinks.length; i++) {{
                        tablinks[i].className = tablinks[i].className.replace(" active", "");
                    }}
                    document.getElementById(tabName).style.display = "block";
                    evt.currentTarget.className += " active";
                }}
            </script>
        </body>
    </html>
    """
    return html_content

async def sync_portfolio_state(msg):
    if "portfolio" in msg:
        contracts = msg["portfolio"].get("contracts", [])
        active_ids = [c["contract_id"] for c in contracts]
        
        if bot_state["active_trade"]:
            if bot_state["contract_id"] not in active_ids:
                await deriv.send({
                    "proposal_open_contract": 1, 
                    "contract_id": bot_state["contract_id"]
                })
                await telegram.send("🔍 <b>Sync:</b> พบออเดอร์จบไปแล้วตอนออฟไลน์ กำลังดึงสถิติกำไร...")
            else:
                await deriv.send({
                    "proposal_open_contract": 1, 
                    "contract_id": bot_state["contract_id"], 
                    "subscribe": 1
                })
                await telegram.send("🛰️ <b>System:</b> กำลังติดตามออเดอร์ที่ค้างอยู่ต่อ...")

async def active_trade_manager(msg):
    if "proposal_open_contract" in msg:
        contract = msg["proposal_open_contract"]
        if not contract: return

        if not bot_state["active_trade"]: 
            return

        c_id = contract.get("contract_id")
        
        if bot_state["active_trade"] and bot_state["contract_id"] is None:
            await update_state({"contract_id": c_id})
            
        # [Fix C] ตรวจสอบ Type-Safe ป้องกันฐานข้อมูลแปลงตัวเลขเป็นอักษรแล้วหาไม่เจอ
        if bot_state["contract_id"] and str(bot_state["contract_id"]) != str(c_id): return

        profit = float(contract.get("profit") or 0.0)
        entry_spot = float(contract.get("entry_spot") or 0.0)
        is_sold = bool(contract.get("is_sold", False)) or contract.get("status") == "sold"

        if entry_spot and bot_state["entry_price"] == 0:
            await update_state({"entry_price": entry_spot})

        if is_sold:
            if local_mem.get("is_processing_close"): 
                return
            local_mem["is_processing_close"] = True

            try:
                new_profit = bot_state.get("total_profit", 0.0) + profit
                wins = bot_state.get("win_count", 0) + (1 if profit > 0 else 0)
                losses = bot_state.get("loss_count", 0) + (1 if profit <= 0 else 0)

                today_str = datetime.now(TH_TZ).strftime("%Y-%m-%d")
                w = 1 if profit > 0 else 0
                l = 1 if profit <= 0 else 0
                
                # บันทึกประวัติรายวันลงตาราง Time-series และดึงยอดกำไรรวมของวันนี้กลับมา
                today_profit = await db.update_daily_record(today_str, profit, w, l)

                # [Fix 8] อัปเดต Cache แบบ Real-time เพื่อให้หน้าเว็บเปลี่ยนทันทีไม่ต้องรอ 5 นาที
                if dashboard_cache["last_updated"] != 0:
                    if today_str not in dashboard_cache["data"]:
                        dashboard_cache["data"][today_str] = {"profit": 0.0, "wins": 0, "losses": 0}
                    dashboard_cache["data"][today_str]["profit"] = today_profit
                    dashboard_cache["data"][today_str]["wins"] += w
                    dashboard_cache["data"][today_str]["losses"] += l

                await update_state({
                    "active_trade": False, "contract_id": None, 
                    "total_profit": new_profit, "win_count": wins, "loss_count": losses,
                    "is_breakeven": False, "signal_type": ""
                })
                
                emoji = "🟢" if profit > 0 else "🔴"
                await telegram.send(f"{emoji} <b>Trade Closed!</b>\nProfit: {profit:.2f} USD\n📅 Today: {today_profit:.2f} USD")
                
                await deriv.send({"forget_all": "proposal_open_contract"})
            finally:
                local_mem["is_processing_close"] = False
            return

async def request_history():
    # [Fix 2] ใช้ next() สร้าง id ที่เป็นเอกลักษณ์ ป้องกัน id ชน 100%
    req_1m = next(global_req_counter)
    req_15m = next(global_req_counter)
    
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 60, "style": "candles", "req_id": req_1m, "subscribe": 1})
    await deriv.send({"ticks_history": SYMBOL, "end": "latest", "count": 300, "granularity": 900, "style": "candles", "req_id": req_15m, "subscribe": 1})
    return req_1m, req_15m

async def trading_loop():
    last_processed_time = None
    req_1m, req_15m = 0, 0

    while True:
        try:
            await deriv.connect()
            
            # [Fix 3] เคลียร์สถานะใน Memory ทันทีที่เชื่อมต่อใหม่ ป้องกันบอทค้างไม่ยอมออกออเดอร์
            local_mem["sell_triggered"] = False
            local_mem["is_processing_close"] = False
            local_mem["last_heartbeat"] = 0
            local_mem["last_1m_candle_time"] = time.time()
            
            await deriv.send({"portfolio": 1})
            for _ in range(5): 
                init_msg = await deriv.receive()
                if init_msg and "portfolio" in init_msg:
                    await sync_portfolio_state(init_msg)
                    break

            req_1m, req_15m = await request_history()

            while True:
                try:
                    msg = await asyncio.wait_for(deriv.receive(), timeout=30.0)
                except asyncio.TimeoutError:
                    print("⏳ Connection frozen (Watchdog). Reconnecting...")
                    await telegram.send("🔄 <b>Watchdog:</b> ตรวจพบอาการค้าง กำลังรีเซ็ตการเชื่อมต่อ...")
                    break 

                if not msg: continue
                
                now = time.time()
                
                # [Fix A] Stream Watchdog: เช็คว่ากราฟ 1 นาทีหยุดอัปเดตไปเกิน 90 วินาทีหรือไม่
                if now - local_mem["last_1m_candle_time"] > 90 and len(candles_1m) > 0:
                    print("⏳ 1m Stream frozen! Reconnecting...")
                    await telegram.send("⚠️ <b>Stream Dead:</b> กราฟ 1 นาทีหยุดอัปเดต กำลังรีเซ็ตการเชื่อมต่อ...")
                    break
                    
                # [Fix B] Independent Heartbeat: กระตุ้นถามสถานะออเดอร์ด้วยตัวเองทุกๆ 20 วินาที
                if bot_state["active_trade"] and bot_state["contract_id"]:
                    if now - local_mem["last_heartbeat"] > 20:
                        local_mem["last_heartbeat"] = now
                        await deriv.send({"proposal_open_contract": 1, "contract_id": bot_state["contract_id"]})
                
                if "error" in msg:
                    err = msg['error'].get('message', 'Unknown')
                    err_lower = err.lower()
                    force_reset_keywords = ["process your trade", "invalid contract", "sold", "expired"]
                    
                    if any(keyword in err_lower for keyword in force_reset_keywords):
                        print(f"Auto-Resetting State due to Error: {err}")
                        await telegram.send(f"🔄 <b>Auto-Reset:</b> รีเซ็ตสถานะบอทให้ว่าง (สาเหตุ: {err})")
                        await update_state({
                            "active_trade": False, "contract_id": None, "is_breakeven": False, 
                            "entry_price": 0, "sl": 0, "tp": 0, "signal_type": ""
                        })
                        local_mem["sell_triggered"] = False
                        await deriv.send({"forget_all": "proposal_open_contract"})
                        await deriv.send({"portfolio": 1})
                    else:
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
                    
                    if granularity == 60:
                        local_mem["last_1m_candle_time"] = time.time()
                    
                    if len(target_deque) > 0:
                        last_candle = target_deque[-1]
                        
                        if open_time - last_candle['time'] > (granularity * 2):
                            await telegram.send(f"⏳ <b>Data Gap Detected ({granularity}s):</b> Refetching...")
                            req_1m, req_15m = await request_history()
                            continue 

                        if open_time == last_candle['time']:
                            for k in ['close', 'high', 'low']: last_candle[k] = float(ohlc[k])
                        elif open_time > last_candle['time']:
                            
                            if granularity == 60:
                                
                                if not bot_state["active_trade"] and len(candles_15m) > 0:
                                    if last_processed_time != last_candle['time']:
                                        last_processed_time = last_candle['time']
                                        
                                        try:
                                            df_1m = pd.DataFrame(tuple(candles_1m))
                                            df_15m = pd.DataFrame(tuple(candles_15m))
                                            
                                            signal, sl_dist, tp_dist = await asyncio.wait_for(
                                                asyncio.to_thread(strategy.analyze, df_1m, df_15m), 
                                                timeout=5.0
                                            )
                                            
                                            if signal:
                                                curr_price = last_candle['close']
                                                sl_price = curr_price - sl_dist if signal == 'BUY' else curr_price + sl_dist
                                                tp_price = curr_price + tp_dist if signal == 'BUY' else curr_price - tp_dist
                                                
                                                stake = 10 
                                                mult = 100
                                                sl_amount = round((sl_dist / curr_price) * mult * stake, 2)
                                                tp_amount = round((tp_dist / curr_price) * mult * stake, 2)

                                                max_sl = stake * 0.95
                                                if sl_amount > max_sl: sl_amount = max_sl

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
                                            await telegram.send("⏱️ <b>Analyzer Timeout:</b> ข้ามแท่งนี้")
                                        except Exception as calc_err:
                                            print(f"Indicator Error: {calc_err}")
                            
                            target_deque.append({"time": open_time, "open": float(ohlc['open']), "high": float(ohlc['high']), "low": float(ohlc['low']), "close": float(ohlc['close'])})

        except websockets.exceptions.ConnectionClosed as e:
            await telegram.send(f"🔌 <b>Connection Dropped:</b> {e.code}. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Critical System Error: {e}")
            await telegram.send(f"❌ <b>System Crash:</b> รีสตาร์ทใน 10วิ")
            await asyncio.sleep(10)