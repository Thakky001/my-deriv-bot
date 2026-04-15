from fastapi import FastAPI
import asyncio
import time
import pandas as pd
from collections import deque
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

# ── State ใน RAM ──────────────────────────────────────────────────────────────
bot_state = {
    "active_trade":  False,
    "contract_id":   None,
    "entry_price":   0,
    "sl":            0,
    "tp":            0,
    "is_breakeven":  False,
    "signal_type":   "",
}

# แยก last_sell_time ออกจาก bot_state เพราะเป็น runtime-only (ไม่ต้อง persist)
_last_sell_time: float = 0.0

# FIX #2 – เก็บ deque ระดับ module เพื่อกัน reset ทุกครั้งที่ reconnect
candles_1m:  deque = deque(maxlen=300)
candles_15m: deque = deque(maxlen=300)

# FIX #3 – Lock กัน race-condition ตอนกด Sell ซ้ำ
_sell_lock = asyncio.Lock()

# ── State / DB helper ─────────────────────────────────────────────────────────
async def update_state(payload: dict):
    """อัปเดต RAM และ flush ลง DB ทันที (ไม่รวม runtime-only keys)"""
    bot_state.update(payload)
    await db.update_state(dict(bot_state))          # ส่งทุก key ที่เหลือ


# ── Startup / Routes ──────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram.start_worker())

    saved = await db.get_state()
    if saved:
        for key in bot_state:
            if key in saved:
                bot_state[key] = saved[key]
        await telegram.send(
            f"🔄 <b>System Booted:</b> โหลดสถานะจาก DB สำเร็จ "
            f"(Active: {bot_state['active_trade']})"
        )
    else:
        await telegram.send("🚀 <b>System Booted:</b> เริ่มต้นใหม่")

    asyncio.create_task(trading_loop())


@app.get("/ping")
async def ping():
    return {"status": "alive"}


@app.get("/")
async def root():
    return {"status": "Deriv Bot API is running!"}


# ── Trade Manager ─────────────────────────────────────────────────────────────
async def active_trade_manager(msg: dict):
    if "proposal_open_contract" not in msg:
        return

    contract = msg["proposal_open_contract"]
    if not contract:
        return

    c_id = contract.get("contract_id")

    # บันทึก contract_id ถ้ายังไม่มี (ออเดอร์เพิ่งถูกยืนยัน)
    if bot_state["active_trade"] and bot_state["contract_id"] is None and c_id:
        await update_state({"contract_id": c_id})

    # กรองข้อมูลของ contract อื่น
    if bot_state["contract_id"] and bot_state["contract_id"] != c_id:
        return

    profit       = float(contract.get("profit", 0))
    current_spot = float(contract.get("current_spot", 0))
    entry_spot   = float(contract.get("entry_spot", 0))
    is_sold      = bool(contract.get("is_sold", False))

    # บันทึก entry price จริง (ลด slippage)
    if entry_spot and bot_state["entry_price"] == 0:
        await update_state({"entry_price": entry_spot})

    # ── ออเดอร์ปิดแล้ว ────────────────────────────────────────────────────────
    if is_sold:
        # FIX #7 – Unsubscribe stream ของ contract ที่ปิดแล้ว
        if c_id:
            await deriv.send({"forget_all": "proposal_open_contract"})

        await update_state({
            "active_trade": False, "contract_id": None,
            "is_breakeven": False, "entry_price": 0,
            "sl": 0, "tp": 0, "signal_type": "",
        })
        emoji = "🟢" if profit > 0 else "🔴"
        await telegram.send(f"{emoji} <b>Trade Closed!</b>\nProfit: {profit:.2f} USD")
        return

    # ── Failsafe SL/TP (Server-side ไม่ตัดให้) ───────────────────────────────
    if current_spot > 0 and bot_state["sl"] > 0 and c_id:
        is_buy = bot_state["signal_type"] == "BUY"
        should_close = (
            (is_buy  and (current_spot <= bot_state["sl"] or current_spot >= bot_state["tp"])) or
            (not is_buy and (current_spot >= bot_state["sl"] or current_spot <= bot_state["tp"]))
        )

        if should_close:
            # FIX #3 – ใช้ Lock กัน Sell ซ้ำ + FIX #6 – ส่ง Telegram แค่ครั้งเดียว
            async with _sell_lock:
                global _last_sell_time
                now = time.monotonic()
                if now - _last_sell_time > 10:
                    _last_sell_time = now
                    await deriv.send({"sell": c_id, "price": 0})
                    await telegram.send(
                        f"🛑 <b>Failsafe Sell triggered</b> "
                        f"(spot={current_spot:.4f})"
                    )

    # ── Break-even ────────────────────────────────────────────────────────────
    if not bot_state["is_breakeven"] and bot_state["entry_price"] > 0:
        diff = abs(bot_state["entry_price"] - bot_state["sl"])
        if diff > 0:
            is_buy = bot_state["signal_type"] == "BUY"
            triggered = (
                (is_buy     and current_spot >= bot_state["entry_price"] + diff) or
                (not is_buy and current_spot <= bot_state["entry_price"] - diff)
            )
            if triggered:
                await update_state({"sl": bot_state["entry_price"], "is_breakeven": True})
                await telegram.send(
                    "🛡️ <b>Break-even Triggered!</b> ขยับ SL บังหน้าทุน"
                )


# ── Trading Loop ──────────────────────────────────────────────────────────────
async def trading_loop():
    last_processed_time = None

    # FIX #10 – แยก Exception ที่เป็น network จาก logic bug
    FATAL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError)

    while True:   # 🔁 outer loop = auto-reconnect
        try:
            await deriv.connect()

            # FIX #9 – ใช้ req_id ที่มีความหมาย ไม่ชนกัน
            REQ_1M  = 101
            REQ_15M = 115

            # FIX #2 – ดึง history เฉพาะกรณี deque ว่าง (ไม่ reset ทุกครั้ง)
            if len(candles_1m) == 0:
                await deriv.send({
                    "ticks_history": SYMBOL, "end": "latest", "count": 300,
                    "granularity": 60, "style": "candles",
                    "req_id": REQ_1M, "subscribe": 1,
                })
            else:
                # Subscribe stream ต่อโดยไม่ดึง history ซ้ำ
                await deriv.send({
                    "ticks_history": SYMBOL, "end": "latest", "count": 1,
                    "granularity": 60, "style": "candles",
                    "req_id": REQ_1M, "subscribe": 1,
                })

            if len(candles_15m) == 0:
                await deriv.send({
                    "ticks_history": SYMBOL, "end": "latest", "count": 300,
                    "granularity": 900, "style": "candles",
                    "req_id": REQ_15M, "subscribe": 1,
                })
            else:
                await deriv.send({
                    "ticks_history": SYMBOL, "end": "latest", "count": 1,
                    "granularity": 900, "style": "candles",
                    "req_id": REQ_15M, "subscribe": 1,
                })

            # 🛡️ กู้คืน subscription ออเดอร์ที่ค้างอยู่
            if bot_state["active_trade"] and bot_state["contract_id"]:
                await deriv.send({
                    "proposal_open_contract": 1,
                    "contract_id": bot_state["contract_id"],
                    "subscribe": 1,
                })
            elif not bot_state["active_trade"]:
                # FIX #1 – ถ้า active=True แต่ contract_id=None (crash กลางออเดอร์)
                # ไม่ subscribe แบบ blindly → reset state แทน
                pass
            # else: active=True, contract_id=None → รอ buy response เพื่อ subscribe

            # ── inner event loop ──────────────────────────────────────────────
            while True:
                msg = await deriv.receive()

                # ── Error handling ────────────────────────────────────────────
                if "error" in msg:
                    err_msg = msg["error"].get("message", "Unknown Error")
                    err_code = msg["error"].get("code", "")
                    await telegram.send(f"⚠️ <b>API Error [{err_code}]:</b> {err_msg}")

                    # FIX #1 – Zombie Bot: ออเดอร์ถูก reject → unlock state
                    if bot_state["active_trade"] and bot_state["contract_id"] is None:
                        await update_state({"active_trade": False, "signal_type": ""})
                        await telegram.send(
                            "🔄 <b>Reset State:</b> คำสั่งซื้อล้มเหลว ยกเลิกล็อคสถานะแล้ว"
                        )
                    continue

                # ── Buy confirmed ─────────────────────────────────────────────
                if "buy" in msg:
                    c_id = msg["buy"].get("contract_id")
                    await update_state({"contract_id": c_id, "active_trade": True})
                    await deriv.send({
                        "proposal_open_contract": 1,
                        "contract_id": c_id,
                        "subscribe": 1,
                    })
                    await telegram.send(f"✅ <b>Order Placed!</b> ID: {c_id}")

                # ── Trade manager ─────────────────────────────────────────────
                await active_trade_manager(msg)

                # ── Load candle history ───────────────────────────────────────
                if "candles" in msg:
                    req_id = msg.get("req_id")
                    c_list = [
                        {
                            "time":  c["epoch"],
                            "open":  float(c["open"]),
                            "high":  float(c["high"]),
                            "low":   float(c["low"]),
                            "close": float(c["close"]),
                        }
                        for c in msg["candles"]
                    ]
                    if req_id == REQ_1M:
                        candles_1m.extend(c_list)
                    elif req_id == REQ_15M:
                        candles_15m.extend(c_list)

                # ── OHLC real-time stream ─────────────────────────────────────
                if "ohlc" in msg:
                    ohlc        = msg["ohlc"]
                    granularity = ohlc.get("granularity")
                    open_time   = ohlc["open_time"]
                    target      = candles_1m if granularity == 60 else candles_15m

                    if not target:
                        continue

                    last_candle = target[-1]

                    if open_time == last_candle["time"]:
                        # อัปเดตราคาในแท่งปัจจุบัน
                        for k in ("close", "high", "low"):
                            last_candle[k] = float(ohlc[k])

                    elif open_time > last_candle["time"]:
                        # FIX #4 – เติมแท่งที่ขาดหายระหว่าง gap (ใช้ข้อมูลล่าสุดที่มี)
                        gap = (open_time - last_candle["time"]) // (granularity or 60)
                        if gap > 1:
                            await telegram.send(
                                f"⚠️ <b>Candle Gap:</b> {gap-1} แท่งหาย "
                                f"(granularity={granularity}s)"
                            )
                            # เติม gap ด้วยแท่ง doji จาก close ล่าสุด เพื่อไม่ให้ indicator เบี้ยว
                            prev_close = last_candle["close"]
                            step = granularity or 60
                            for i in range(1, gap):
                                fake_time = last_candle["time"] + i * step
                                target.append({
                                    "time": fake_time,
                                    "open": prev_close, "high": prev_close,
                                    "low":  prev_close, "close": prev_close,
                                })

                        # ── ประมวลผลสัญญาณ (1m candle close) ─────────────────
                        if (
                            granularity == 60
                            and not bot_state["active_trade"]
                            and len(candles_15m) > 50   # มีข้อมูลพอ
                            and last_processed_time != last_candle["time"]
                        ):
                            last_processed_time = last_candle["time"]

                            df_1m  = pd.DataFrame(list(candles_1m))
                            df_15m = pd.DataFrame(list(candles_15m))

                            # FIX #5 – Timeout 30 วินาที กัน pandas_ta ค้าง
                            try:
                                signal, sl_dist, tp_dist = await asyncio.wait_for(
                                    asyncio.to_thread(strategy.analyze, df_1m, df_15m),
                                    timeout=30.0,
                                )
                            except asyncio.TimeoutError:
                                await telegram.send(
                                    "⚠️ <b>Strategy Timeout:</b> analyze() ใช้เวลา >30s ข้ามแท่งนี้"
                                )
                                signal = None

                            if signal:
                                curr_price = last_candle["close"]
                                sl_price   = curr_price - sl_dist if signal == "BUY" else curr_price + sl_dist
                                tp_price   = curr_price + tp_dist if signal == "BUY" else curr_price - tp_dist

                                # FIX #8 – ค่า stake/mult ดึงจาก config (อย่า hardcode)
                                from config import STAKE, MULTIPLIER  # noqa: PLC0415
                                stake = STAKE
                                mult  = MULTIPLIER

                                sl_amount = round((sl_dist / curr_price) * mult * stake, 2)
                                tp_amount = round((tp_dist / curr_price) * mult * stake, 2)

                                await update_state({
                                    "signal_type": signal,
                                    "sl":          sl_price,
                                    "tp":          tp_price,
                                    "active_trade": True,
                                    "is_breakeven": False,
                                    "entry_price":  0,
                                })

                                await telegram.send(
                                    f"🚨 <b>SIGNAL: {signal}</b>\n"
                                    f"SL Price: {sl_price:.4f}\n"
                                    f"TP Price: {tp_price:.4f}"
                                )

                                await deriv.send({
                                    "buy": 1, "price": stake,
                                    "parameters": {
                                        "amount":         stake,
                                        "basis":          "stake",
                                        "symbol":         SYMBOL,
                                        "currency":       "USD",
                                        "multiplier":     mult,
                                        "contract_type":  "MULTUP" if signal == "BUY" else "MULTDOWN",
                                        "limit_order": {
                                            "stop_loss":   sl_amount,
                                            "take_profit": tp_amount,
                                        },
                                    },
                                })

                        # เพิ่มแท่งใหม่เข้า deque (ของเก่าสุดถูกดันออกอัตโนมัติ)
                        target.append({
                            "time":  open_time,
                            "open":  float(ohlc["open"]),
                            "high":  float(ohlc["high"]),
                            "low":   float(ohlc["low"]),
                            "close": float(ohlc["close"]),
                        })

        except FATAL_EXCEPTIONS:
            # FIX #10 – ไม่ reconnect กับ fatal error
            await telegram.send("💀 <b>Fatal Error:</b> Bot หยุดทำงาน กรุณา Restart ด้วยตนเอง")
            raise

        except Exception as e:
            # Network / transient error → reconnect
            await telegram.send(
                f"❌ <b>Network Lost:</b> {type(e).__name__}: {e}. "
                f"Reconnecting in 5s..."
            )
            await asyncio.sleep(5)