import pandas as pd
import pandas_ta_classic as ta  # แก้ไขจาก pandas_ta เป็น pandas_ta_classic


class Strategy:
    def __init__(self):
        self.max_rows = 300

    def analyze(self, df_1m: pd.DataFrame, df_15m: pd.DataFrame):
        # ตัดข้อมูลเพื่อประหยัด Memory
        df_1m = df_1m.tail(self.max_rows).copy()
        df_15m = df_15m.tail(self.max_rows).copy()

        # ─────────────────────────────────────────
        # คำนวณ Indicators ทั้งหมด
        # ─────────────────────────────────────────

        # 15m Indicators
        df_15m['EMA20'] = ta.ema(df_15m['close'], length=20)
        df_15m['EMA50'] = ta.ema(df_15m['close'], length=50)
        df_15m['ATR']   = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)

        adx_15m = ta.adx(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        if adx_15m is not None:
            df_15m = pd.concat([df_15m, adx_15m], axis=1)

        # 1m Indicators
        df_1m['EMA20'] = ta.ema(df_1m['close'], length=20)
        df_1m['EMA50'] = ta.ema(df_1m['close'], length=50)
        df_1m['RSI']   = ta.rsi(df_1m['close'], length=14)
        df_1m['ATR']   = ta.atr(df_1m['high'], df_1m['low'], df_1m['close'], length=14)

        # ─────────────────────────────────────────
        # Guard: ต้องมีข้อมูลพอสำหรับ slope (ดึง -5)
        # ─────────────────────────────────────────
        if len(df_1m) < 6 or len(df_15m) < 6:
            return None, 0, 0

        # ใช้แท่งที่ปิดสมบูรณ์แล้วเสมอ
        last_15m = df_15m.iloc[-2]
        last_1m  = df_1m.iloc[-2]

        # ─────────────────────────────────────────
        # ดึงค่า Indicator ที่ต้องใช้
        # ─────────────────────────────────────────
        try:
            adx_val = last_15m['ADX_14']
        except KeyError:
            return None, 0, 0

        rsi_val     = last_1m['RSI']
        atr_1m_val  = last_1m['ATR']
        atr_15m_val = last_15m['ATR']

        # ─────────────────────────────────────────
        # [Filter 1] Trend Filter 15m
        # ─────────────────────────────────────────
        uptrend_15m   = last_15m['EMA20'] > last_15m['EMA50'] and adx_val > 25
        downtrend_15m = last_15m['EMA20'] < last_15m['EMA50'] and adx_val > 25
        
        # เช็คสภาวะตลาดเงียบ (ADX < 20 แปลว่าไม่มีเทรนด์)
        is_quiet = adx_val < 20

        # ─────────────────────────────────────────
        # [Filter 2] EMA Slope Filter
        # ─────────────────────────────────────────
        ema20_slope_1m  = df_1m['EMA20'].iloc[-2]  - df_1m['EMA20'].iloc[-5]
        ema20_slope_15m = df_15m['EMA20'].iloc[-2] - df_15m['EMA20'].iloc[-5]

        is_sloping_up   = ema20_slope_1m > 0 and ema20_slope_15m > 0
        is_sloping_down = ema20_slope_1m < 0 and ema20_slope_15m < 0

        # ─────────────────────────────────────────
        # [Filter 3] 1m Trend Alignment
        # ─────────────────────────────────────────
        ema_align_buy  = last_1m['EMA20'] > last_1m['EMA50']
        ema_align_sell = last_1m['EMA20'] < last_1m['EMA50']

        # ─────────────────────────────────────────
        # [Filter 4] Pin Bar / Rejection Candle
        # ─────────────────────────────────────────
        body_size   = abs(last_1m['close'] - last_1m['open'])
        candle_size = last_1m['high'] - last_1m['low']

        lower_wick  = min(last_1m['open'], last_1m['close']) - last_1m['low']
        upper_wick  = last_1m['high'] - max(last_1m['open'], last_1m['close'])

        is_rejection_buy  = False
        is_rejection_sell = False
        if candle_size > 0:
            is_rejection_buy  = lower_wick > body_size * 1.5
            is_rejection_sell = upper_wick > body_size * 1.5

        # ─────────────────────────────────────────
        # [Filter 5] Candle Trigger
        # ─────────────────────────────────────────
        bullish_candle = (
            last_1m['close'] > last_1m['open'] and
            last_1m['low']   <= last_1m['EMA20'] and
            last_1m['close'] > last_1m['EMA20']
        )
        bearish_candle = (
            last_1m['close'] < last_1m['open'] and
            last_1m['high']  >= last_1m['EMA20'] and
            last_1m['close'] < last_1m['EMA20']
        )

        # ─────────────────────────────────────────
        # [Filter 6] Price Distance Filter
        # ─────────────────────────────────────────
        distance_from_ema = abs(last_1m['close'] - last_1m['EMA20'])
        is_near_ema = distance_from_ema <= atr_1m_val * 0.5

        # ─────────────────────────────────────────
        # [Filter 7] ATR Spike Filter
        # ─────────────────────────────────────────
        atr_median     = df_1m['ATR'].rolling(50).median().iloc[-2]
        atr_too_spiky  = atr_1m_val > atr_median * 2.0

        # ─────────────────────────────────────────
        # รวม Condition ทั้งหมด
        # ─────────────────────────────────────────
        signal = None

        buy_condition = (
            uptrend_15m        and 
            is_sloping_up      and 
            ema_align_buy      and 
            (45 <= rsi_val <= 65) and 
            bullish_candle     and 
            is_rejection_buy   and 
            is_near_ema        and 
            not atr_too_spiky
        )

        sell_condition = (
            downtrend_15m      and 
            is_sloping_down    and 
            ema_align_sell     and 
            (35 <= rsi_val <= 55) and 
            bearish_candle     and 
            is_rejection_sell  and 
            is_near_ema        and 
            not atr_too_spiky
        )

        if buy_condition:
            signal = 'BUY'
        elif sell_condition:
            signal = 'SELL'
        elif is_quiet:
            signal = 'QUIET'

        # ─────────────────────────────────────────
        # คำนวณ SL/TP อิงจาก ATR 15m (R:R = 1:1.75)
        # ─────────────────────────────────────────
        sl_dist = atr_15m_val * 1.6
        tp_dist = atr_15m_val * 2.8

        return signal, sl_dist, tp_dist