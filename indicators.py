import pandas as pd
import pandas_ta_classic as ta


class Strategy:
    def __init__(self):
        self.max_rows = 300

    def analyze(self, df_1m: pd.DataFrame, df_15m: pd.DataFrame):
        df_1m  = df_1m.tail(self.max_rows).copy()
        df_15m = df_15m.tail(self.max_rows).copy()

        # =========================
        # INDICATORS
        # =========================
        df_15m['EMA50'] = ta.ema(df_15m['close'], length=50)
        df_15m['ATR']   = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)

        df_1m['EMA20'] = ta.ema(df_1m['close'], length=20)
        df_1m['ATR']   = ta.atr(df_1m['high'], df_1m['low'], df_1m['close'], length=14)

        if len(df_1m) < 30 or len(df_15m) < 30:
            return None, 0, 0

        last = df_1m.iloc[-2]
        prev = df_1m.iloc[-3]
        last_15m = df_15m.iloc[-2]

        atr_1m  = last['ATR']
        atr_15m = last_15m['ATR']

        # =========================
        # TREND
        # =========================
        uptrend   = last_15m['close'] > last_15m['EMA50']
        downtrend = last_15m['close'] < last_15m['EMA50']

        # =========================
        # BREAKOUT
        # =========================
        high_10 = df_1m['high'].rolling(10).max().iloc[-2]
        low_10  = df_1m['low'].rolling(10).min().iloc[-2]

        breakout_buy  = last['close'] > high_10
        breakout_sell = last['close'] < low_10

        # =========================
        # EXPANSION (แท่งต้องใหญ่ขึ้น)
        # =========================
        body_now  = abs(last['close'] - last['open'])
        body_prev = abs(prev['close'] - prev['open'])

        expansion = body_now > body_prev * 1.2

        # =========================
        # MOMENTUM ACCELERATION
        # =========================
        momentum_up = (last['close'] - prev['close']) > atr_1m * 0.3
        momentum_dn = (prev['close'] - last['close']) > atr_1m * 0.3

        # =========================
        # VOLATILITY FILTER
        # =========================
        atr_mean = df_1m['ATR'].rolling(30).mean().iloc[-2]
        good_vol = atr_1m > atr_mean * 0.7

        # =========================
        # RE-ENTRY LOGIC
        # =========================
        pullback_buy  = last['low'] <= last['EMA20'] and last['close'] > last['EMA20']
        pullback_sell = last['high'] >= last['EMA20'] and last['close'] < last['EMA20']

        # =========================
        # SIGNAL
        # =========================
        signal = None

        # 🔥 Primary Entry
        if breakout_buy and expansion and momentum_up and uptrend and good_vol:
            signal = "BUY"

        elif breakout_sell and expansion and momentum_dn and downtrend and good_vol:
            signal = "SELL"

        # 🔥 Re-entry (กำลังวิ่งแล้ว)
        elif uptrend and pullback_buy and momentum_up:
            signal = "BUY"

        elif downtrend and pullback_sell and momentum_dn:
            signal = "SELL"

        # =========================
        # SL / TP
        # =========================
        sl_dist = atr_15m * 1.2
        tp_dist = atr_15m * 2.5   # RR ~ 1:2+

        return signal, sl_dist, tp_dist