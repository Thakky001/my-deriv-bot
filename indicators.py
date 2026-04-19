import pandas as pd
import pandas_ta_classic as ta  # แก้ไขจาก pandas_ta เป็น pandas_ta_classic


class Strategy:
    def __init__(self):
        self.max_rows = 300

    def analyze(self, df_1m: pd.DataFrame, df_15m: pd.DataFrame):
        # ตัดข้อมูลเพื่อประหยัด Memory
        df_1m = df_1m.tail(self.max_rows).copy()
        df_15m = df_15m.tail(self.max_rows).copy()

        # คำนวณ 15m Indicators ผ่าน pandas_ta_classic
        df_15m['EMA20'] = ta.ema(df_15m['close'], length=20)
        df_15m['EMA50'] = ta.ema(df_15m['close'], length=50)
        adx_15m = ta.adx(df_15m['high'], df_15m['low'],
                         df_15m['close'], length=14)
        if adx_15m is not None:
            df_15m = pd.concat([df_15m, adx_15m], axis=1)

        # คำนวณ 1m Indicators ผ่าน pandas_ta_classic
        df_1m['EMA20'] = ta.ema(df_1m['close'], length=20)
        df_1m['EMA50'] = ta.ema(df_1m['close'], length=50)
        df_1m['RSI'] = ta.rsi(df_1m['close'], length=14)
        df_1m['ATR'] = ta.atr(df_1m['high'], df_1m['low'],
                              df_1m['close'], length=14)

        # [แก้ไขข้อ 1] ลอจิกแท่งเทียนล่าสุดที่ปิดแล้ว (Closed Candle)
        # เปลี่ยนเช็คความยาวเพื่อให้มั่นใจว่าดึง index -2 ได้
        if len(df_1m) < 2 or len(df_15m) < 2:
            return None, 0, 0

        # เปลี่ยนเป็น .iloc[-2] เพื่อใช้แท่งเทียนที่ปิดสมบูรณ์แล้ว 100%
        last_15m = df_15m.iloc[-2]
        last_1m = df_1m.iloc[-2]

        try:
            # pandas_ta_classic จะใช้ชื่อคอลัมน์เหมือนต้นฉบับ
            adx_val = last_15m['ADX_14']
        except KeyError:
            return None, 0, 0

        # Trend Filter 15m
        uptrend_15m = last_15m['EMA20'] > last_15m['EMA50'] and adx_val > 25
        downtrend_15m = last_15m['EMA20'] < last_15m['EMA50'] and adx_val > 25

        # 1m Triggers (ทริคที่ 1: ทิ้งไส้ชนเส้น EMA แล้วดึงกลับมาปิดได้)
        bullish_candle = (last_1m['close'] > last_1m['open']) and (
            last_1m['low'] <= last_1m['EMA20']) and (last_1m['close'] > last_1m['EMA20'])
        bearish_candle = (last_1m['close'] < last_1m['open']) and (
            last_1m['high'] >= last_1m['EMA20']) and (last_1m['close'] < last_1m['EMA20'])

        rsi_val = last_1m['RSI']
        atr_val = last_1m['ATR']

        # กรองแท่งเทียน (ทริคที่ 2: หลีกเลี่ยงแท่ง Doji เนื้อเทียนต้องมีขนาดใหญ่กว่า 50% ของความยาวแท่ง)
        body_size = abs(last_1m['close'] - last_1m['open'])
        candle_size = last_1m['high'] - last_1m['low']

        is_strong_candle = False
        if candle_size > 0:
            is_strong_candle = body_size > (candle_size * 0.5)

        signal = None
        # นำตัวแปร is_strong_candle เข้ามาเช็คเป็นเงื่อนไขเพิ่มเติมก่อนออก Signal
        # [แก้ไขข้อ 2] ขยายกรอบ RSI เป็น 45-70 สำหรับ Buy และ 30-55 สำหรับ Sell เพื่อลด Missed Opportunities
        if uptrend_15m and last_1m['EMA20'] > last_1m['EMA50'] and (45 <= rsi_val <= 70) and bullish_candle and is_strong_candle:
            signal = 'BUY'
        elif downtrend_15m and last_1m['EMA20'] < last_1m['EMA50'] and (30 <= rsi_val <= 55) and bearish_candle and is_strong_candle:
            signal = 'SELL'

        # [แก้ไขข้อ 3] คำนวณระยะ SL/TP ชดเชย Slippage
        # ขยาย SL เป็น 1.6 เพื่อกันสะบัด และหด TP เป็น 1.8 เพื่อให้ชนง่ายขึ้นแม้ได้ราคาเปิดที่แย่ลง
        sl_dist = atr_val * 1.6
        tp_dist = atr_val * 1.8

        return signal, sl_dist, tp_dist