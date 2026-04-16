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
        adx_15m = ta.adx(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
        if adx_15m is not None:
            df_15m = pd.concat([df_15m, adx_15m], axis=1)

        # คำนวณ 1m Indicators ผ่าน pandas_ta_classic
        df_1m['EMA20'] = ta.ema(df_1m['close'], length=20)
        df_1m['EMA50'] = ta.ema(df_1m['close'], length=50)
        df_1m['RSI'] = ta.rsi(df_1m['close'], length=14)
        df_1m['ATR'] = ta.atr(df_1m['high'], df_1m['low'], df_1m['close'], length=14)

        # ลอจิกแท่งเทียนล่าสุด (Closed Candle)
        if df_1m.empty or df_15m.empty:
            return None, 0, 0
            
        last_15m = df_15m.iloc[-1]
        last_1m = df_1m.iloc[-1]
        
        try:
            # pandas_ta_classic จะใช้ชื่อคอลัมน์เหมือนต้นฉบับ
            adx_val = last_15m['ADX_14']
        except KeyError:
            return None, 0, 0

        # Trend Filter 15m
        uptrend_15m = last_15m['EMA20'] > last_15m['EMA50'] and adx_val > 25
        downtrend_15m = last_15m['EMA20'] < last_15m['EMA50'] and adx_val > 25

        # 1m Triggers (ทริคที่ 1: ทิ้งไส้ชนเส้น EMA แล้วดึงกลับมาปิดได้)
        bullish_candle = (last_1m['close'] > last_1m['open']) and (last_1m['low'] <= last_1m['EMA20']) and (last_1m['close'] > last_1m['EMA20'])
        bearish_candle = (last_1m['close'] < last_1m['open']) and (last_1m['high'] >= last_1m['EMA20']) and (last_1m['close'] < last_1m['EMA20'])
        
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
        if uptrend_15m and last_1m['EMA20'] > last_1m['EMA50'] and (50 <= rsi_val <= 65) and bullish_candle and is_strong_candle:
            signal = 'BUY'
        elif downtrend_15m and last_1m['EMA20'] < last_1m['EMA50'] and (35 <= rsi_val <= 50) and bearish_candle and is_strong_candle:
            signal = 'SELL'

        # คำนวณระยะ SL/TP (ทริคที่ 3: ปรับเป้า TP ให้แคบลงเป็น 2.0 เพื่อเพิ่ม Win Rate)
        sl_dist = atr_val * 1.5
        tp_dist = atr_val * 2.0

        return signal, sl_dist, tp_dist