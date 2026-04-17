# 🚀 คู่มือการติดตั้งและใช้งาน My-Deriv-Bot แบบละเอียดทุกขั้นตอน

การจะทำให้บอทตัวนี้ทำงานได้สมบูรณ์แบบ คุณจะต้องเตรียม 3 ส่วนหลักๆ คือ บัญชีเทรด (Deriv), ระบบแจ้งเตือน (Telegram), และฐานข้อมูล (Supabase) ทำตามขั้นตอนด้านล่างนี้ตามลำดับได้เลยครับ

## ขั้นตอนที่ 1: การเตรียมบัญชีและ API (Prerequisites)

# 1.1 ตั้งค่า Deriv API (เพื่อใช้ดึงกราฟและส่งคำสั่งเทรด)

ล็อกอินเข้าบัญชี Deriv ของคุณ

ไปที่เว็บไซต์ Deriv API (https://api.deriv.com/)

ล็อกอินแล้วไปที่แท็บ Manage App

กดสร้างแอปพลิเคชันใหม่ (Register any app) กรอกชื่อบอทของคุณ และคุณจะได้ App ID (ตัวเลข 4-5 หลัก) มา ให้จดเก็บไว้

กลับไปที่หน้าเทรดปกติของ Deriv ไปที่เมนู Settings > Security & Safety > API Token

ในหน้าสร้าง Token ให้คุณติ๊กเลือกสิทธิ์ (Scopes) 2 อย่างคือ:

✅ Read (สำหรับอ่านกราฟและยอดเงิน)

✅ Trade (สำหรับส่งคำสั่งซื้อขาย)

ตั้งชื่อ Token ว่า "MyBotToken" แล้วกด Create

กดปุ่ม Copy Token ที่ได้มา แล้วจดเก็บไว้ (นี่คือ DERIV_TOKEN)

# 1.2 ตั้งค่า Telegram Bot (เพื่อรับการแจ้งเตือน)

เปิดแอป Telegram ค้นหาบอทที่ชื่อว่า @BotFather (มีติ๊กถูกสีฟ้า)

พิมพ์คำสั่ง /newbot แล้วตั้งชื่อบอทของคุณตามที่ระบบถาม

เมื่อสร้างเสร็จ BotFather จะให้ข้อความยาวๆ ที่เรียกว่า HTTP API Token (เช่น 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11) ให้จดเก็บไว้ (นี่คือ TELEGRAM_TOKEN)

ค้นหาบอทที่ชื่อว่า @userinfobot หรือ @RawDataBot ใน Telegram แล้วกด Start

บอทจะตอบกลับมาพร้อมกับเลข Id ของคุณ (ตัวเลขประมาณ 9-10 หลัก) ให้จดเลขนี้ไว้ (นี่คือ TELEGRAM_CHAT_ID)

สำคัญ: อย่าลืมทักทายบอทที่คุณเพิ่งสร้างขึ้นมาเอง 1 ครั้ง (กด Start) เพื่อเป็นการเปิดช่องทางการแชทให้บอทส่งข้อความหาคุณได้

# 1.3 ตั้งค่า Supabase (เพื่อเก็บฐานข้อมูลและสถิติการเทรด)

ไปที่ https://supabase.com/ แล้วสมัครบัญชี (ฟรี)

กด New Project ตั้งชื่อโปรเจกต์ (เช่น deriv-bot-db) และตั้งรหัสผ่าน Database

รอระบบสร้างโปรเจกต์ประมาณ 2-3 นาที

เมื่อสร้างเสร็จ ให้ไปที่เมนู Project Settings (รูปเฟืองซ้ายล่าง) > เลือกเมนู API

คุณจะเห็นหัวข้อ Project URL ให้ก๊อปปี้ URL มาจดไว้ (นี่คือ SUPABASE_URL)

ในหน้าเดียวกัน หัวข้อ Project API keys เลื่อนลงมาล่างสุดที่หัวข้อ Secret keys ให้ก๊อปปี้ Key ลับของคุณมา (นี่คือ SUPABASE_KEY สำหรับให้บอทเขียนฐานข้อมูล)

## ขั้นตอนที่ 2: การสร้างตารางฐานข้อมูลใน Supabase

ขั้นตอนนี้สำคัญมาก บอทเวอร์ชัน Time-series จะใช้ 2 ตารางร่วมกัน

**ตารางที่ 1: สำหรับสถานะบอท (bot_state)**

1. ในโปรเจกต์ Supabase เลือกเมนู Table Editor (ไอคอนรูปตารางด้านซ้าย)
2. กดปุ่ม Create a new table
3. ตั้งชื่อ Name ว่า bot_state
4. ปิด Enable Row Level Security (RLS) (เอาเครื่องหมายติ๊กถูกออก)
5. ในหัวข้อ Columns ระบบจะสร้าง id มาให้แล้ว ให้กด Add column เพิ่ม:

- คอลัมน์ active_trade เลือก Type เป็น bool (ค่าเริ่มต้น = FALSE)
- คอลัมน์ contract_id เลือก Type เป็น int8 (ปล่อยว่าง)
- คอลัมน์ entry_price เลือก Type เป็น float8 (ค่าเริ่มต้น = 0)
- คอลัมน์ sl เลือก Type เป็น float8 (ค่าเริ่มต้น = 0)
- คอลัมน์ tp เลือก Type เป็น float8 (ค่าเริ่มต้น = 0)
- คอลัมน์ is_breakeven เลือก Type เป็น bool (ค่าเริ่มต้น = FALSE)
- คอลัมน์ signal_type เลือก Type เป็น text (ปล่อยว่าง)
- คอลัมน์ total_profit เลือก Type เป็น float8 (ค่าเริ่มต้น = 0)
- คอลัมน์ win_count เลือก Type เป็น int8 (ค่าเริ่มต้น = 0)
- คอลัมน์ loss_count เลือก Type เป็น int8 (ค่าเริ่มต้น = 0)

6. กด Save
7. **ขั้นตอนบังคับ:** กดปุ่ม Insert row สร้างข้อมูลแถวแรก ปล่อยทุกอย่างเป็นค่าเริ่มต้นแล้วกด Save เพื่อให้มีข้อมูลแถว `id=1` ทิ้งไว้

**ตารางที่ 2: สำหรับเก็บสถิติรายวันแบบ Time-series (daily_history)**

1. กดปุ่ม New table อีกครั้ง
2. ตั้งชื่อ Name ว่า daily_history
3. ปิด Enable Row Level Security (RLS) ด้วย
4. ในหัวข้อ Columns ให้กด Add column เพื่อเพิ่ม:

- คอลัมน์ date เลือก Type เป็น date
- คอลัมน์ profit เลือก Type เป็น float8 (ค่าเริ่มต้น = 0)
- คอลัมน์ win_count เลือก Type เป็น int8 (ค่าเริ่มต้น = 0)
- คอลัมน์ loss_count เลือก Type เป็น int8 (ค่าเริ่มต้น = 0)

5. กด Save (ตารางนี้ไม่ต้องกด Insert row บอทจะสร้างของแต่ละวันขึ้นมาเองโดยอัตโนมัติ)

-- ====================================================================
-- 1. สร้างตารางสถานะบอท (bot_state) (ถอด daily_stats ออกตามระบบใหม่)
-- ====================================================================
CREATE TABLE bot_state (
id INT PRIMARY KEY,
active_trade BOOLEAN DEFAULT FALSE,
contract_id BIGINT,
entry_price DOUBLE PRECISION DEFAULT 0.0,
sl DOUBLE PRECISION DEFAULT 0.0,
tp DOUBLE PRECISION DEFAULT 0.0,
is_breakeven BOOLEAN DEFAULT FALSE,
signal_type TEXT,
total_profit DOUBLE PRECISION DEFAULT 0.0,
win_count BIGINT DEFAULT 0,
loss_count BIGINT DEFAULT 0
);

-- ปิดระบบ RLS เพื่อให้บอทอ่าน/เขียนข้อมูลได้อิสระ
ALTER TABLE bot_state DISABLE ROW LEVEL SECURITY;

-- แถวบังคับ: สร้างข้อมูลตั้งต้นในแถวที่ id = 1 เพื่อให้บอทใช้อัปเดตทับ
INSERT INTO bot_state (
id, active_trade, contract_id, entry_price, sl, tp,
is_breakeven, signal_type, total_profit, win_count, loss_count
) VALUES (
1, FALSE, NULL, 0.0, 0.0, 0.0,
FALSE, '', 0.0, 0, 0
);

-- ====================================================================
-- 2. สร้างตารางเก็บสถิติรายวันแบบ Time-series (daily_history)
-- ====================================================================
CREATE TABLE daily_history (
date DATE PRIMARY KEY,
profit DOUBLE PRECISION DEFAULT 0.0,
win_count BIGINT DEFAULT 0,
loss_count BIGINT DEFAULT 0
);

-- ปิดระบบ RLS เพื่อให้บอทอ่าน/เขียนข้อมูลได้อิสระ
ALTER TABLE daily_history DISABLE ROW LEVEL SECURITY;

## ขั้นตอนที่ 3: ติดตั้งโปรแกรมและตั้งค่าโค้ด (Local Setup)

ติดตั้งโปรแกรม Python 3.11 ขึ้นไปลงในคอมพิวเตอร์ของคุณ (ตอนติดตั้งอย่าลืมติ๊กช่อง "Add Python to PATH")

ดาวน์โหลดโค้ดบอททั้งหมดมาไว้ในโฟลเดอร์เดียวกัน

เปิด Command Prompt (หรือ Terminal) แล้วพิมพ์คำสั่งเพื่อติดตั้งไลบรารีที่จำเป็น:

Bash
pip install -r requirements.txt
สร้างไฟล์ใหม่ในโฟลเดอร์โปรเจกต์ ตั้งชื่อว่า .env (ต้องมีจุดข้างหน้าด้วย)

เปิดไฟล์ .env ด้วย Notepad แล้วนำค่าที่จดไว้จากขั้นตอนที่ 1 มาใส่ให้ครบถ้วนตามรูปแบบนี้:

ข้อมูลโค้ด
DERIV*APP_ID="12345"
DERIV_TOKEN="token*ที่ได้จากderiv*ของคุณ"
SUPABASE_URL="https://xxx.supabase.co"
SUPABASE_KEY="sb_secret*คีย์ลับยาวๆของsupabase"
TELEGRAM_TOKEN="1234:ABCDEF..."
TELEGRAM_CHAT_ID="123456789"
(หมายเหตุ: นำข้อความไปใส่ในเครื่องหมายคำพูดเลย โดยไม่ต้องมีช่องว่าง)

## ขั้นตอนที่ 4: การเปิดใช้งานบอทและการดู Dashboard (Running the Bot)

เปิด Command Prompt หรือ Terminal ชี้ path ไปที่โฟลเดอร์โปรเจกต์ของคุณ

พิมพ์คำสั่งเพื่อสตาร์ทบอท:

Bash
uvicorn main:app --host 0.0.0.0 --port 8000
รอสักครู่ คุณควรจะเห็นข้อความแจ้งเตือนเด้งเข้าแอป Telegram ของคุณว่า "🔄 System Booted: โหลด State สำเร็จ" และ "✅ Connected to Deriv API"

การดูผลประกอบการ: เปิดเว็บเบราว์เซอร์แล้วพิมพ์ URL:

Plaintext
http://localhost:8000
คุณจะพบกับ 🤖 Bot Dashboard ที่แสดงข้อมูล All-time, สรุปรายเดือน และสถิติรายวันแบบ Real-time ทันที

## ☁️ ขั้นตอนที่ 5: การนำบอทรันบน Cloud ให้ทำงาน 24 ชั่วโมง (Deployment)

หากคุณปิดคอมพิวเตอร์ บอทก็จะหยุดทำงาน เพื่อให้บอททำงาน 24 ชั่วโมง แนะนำให้นำไปรันบนเว็บ Server ฟรีอย่าง Render.com หรือ Heroku:

สมัครบัญชี Render.com และผูกกับ GitHub

อัปโหลดโค้ดทั้งหมด (รวมถึงไฟล์ Procfile และ requirements.txt) ขึ้น GitHub ของคุณ

ใน Render ให้กดสร้าง New Web Service แล้วเลือก Repository ของคุณ

กำหนดค่า Environment Variables: นำค่าจากไฟล์ .env ทั้งหมดไปใส่ในหมวด Environment Variables ของ Render ให้ครบ (ห้ามใส่เครื่องหมาย "")

กด Deploy และรอระบบรันโค้ด

สำคัญมาก: เมื่อ Deploy ผ่านแล้ว คุณจะได้ URL ของแอปพลิเคชันมา (เช่น https://your-bot-url.com/ping) ให้นำ URL นี้ไปสมัครเว็บ UptimeRobot.com (ฟรี) แล้วตั้งค่าให้มันยิงคำสั่ง Ping เข้ามาที่เว็บของคุณทุกๆ 5 นาที เพื่อป้องกันไม่ให้เซิร์ฟเวอร์ฟรีของคุณ "หลับ" เวลาไม่มีคนใช้งานครับ
