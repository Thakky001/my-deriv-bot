import pandas as pd
import pandas_ta as ta

class Strategy:
    def __init__(self):
        self.max_rows = 300

    def analyze(self, df_1m: pd.DataFrame, df_15m: pd.DataFrame):
        # ตัดข้อมูลเพื่อประหยัด Memory
        df_1m = df_1m.tail(self.max_rows).copy()
        df_15m = df_15m.tail(self.max_rows).copy()

        # คำนวณ 15m Indicators
        df_15m['EMA20'] = ta.ema(df_15m['close'], length=20)
        df_15m['EMA50'] = ta.ema(df_15m['close'], length=50)
        adx_15m = ta.adx(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        if adx_15m is not None:
            df_15m = pd.concat([df_15m, adx_15m], axis=1)

        # คำนวณ 1m Indicators
        df_1m['EMA20'] = ta.ema(df_1m['close'], length=20)
        df_1m['EMA50'] = ta.ema(df_1m['close'], length=50)
        df_1m['RSI'] = ta.rsi(df_1m['close'], length=14)
        df_1m['ATR'] = ta.atr(df_1m['high'], df_1m['low'], df_1m['close'], length=14)

        # ลอจิกแท่งเทียนล่าสุด (Closed Candle)
        last_15m = df_15m.iloc[-1]
        last_1m = df_1m.iloc[-1]
        
        try:
            adx_val = last_15m['ADX_14']
        except KeyError:
            return None, 0, 0

        # Trend Filter 15m
        uptrend_15m = last_15m['EMA20'] > last_15m['EMA50'] and adx_val > 25
        downtrend_15m = last_15m['EMA20'] < last_15m['EMA50'] and adx_val > 25

        # 1m Triggers
        bullish_candle = last_1m['close'] > last_1m['open']
        bearish_candle = last_1m['close'] < last_1m['open']
        rsi_val = last_1m['RSI']
        atr_val = last_1m['ATR']

        signal = None
        if uptrend_15m and last_1m['EMA20'] > last_1m['EMA50'] and (50 <= rsi_val <= 65) and bullish_candle:
            signal = 'BUY'
        elif downtrend_15m and last_1m['EMA20'] < last_1m['EMA50'] and (35 <= rsi_val <= 50) and bearish_candle:
            signal = 'SELL'

        sl_dist = atr_val * 1.5
        tp_dist = atr_val * 3.0

        return signal, sl_dist, tp_dist