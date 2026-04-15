# 📖 My-Deriv-Bot (Trading Bot & Stat Tracker)

**My-Deriv-Bot** คือบอทเทรดอัตโนมัติที่เชื่อมต่อกับแพลตฟอร์ม Deriv (ผ่าน WebSockets API) ออกแบบมาเพื่อรันกลยุทธ์การเทรดแบบ Trend Following บนสัญญาประเภท Multiplier มีระบบจัดการความเสี่ยงอัตโนมัติ แจ้งเตือนผ่าน Telegram และบันทึกสถานะการเทรดลงฐานข้อมูลเพื่อป้องกันความผิดพลาดจากระบบล่ม

---

## 🌟 ฟีเจอร์หลัก (Key Features)

* **Automated Trading:** วิเคราะห์และเปิดออเดอร์ (Buy/Sell) อัตโนมัติด้วย Multiplier Contracts
* **Dual-Timeframe Analysis:** วิเคราะห์เทรนด์หลักที่ 15 นาที และหาจุดเข้าแม่นยำที่ 1 นาที
* **Server-Side SL/TP:** ส่งค่า Stop Loss และ Take Profit ไปฝากไว้ที่เซิร์ฟเวอร์ Deriv ตั้งแต่ตอนเปิดออเดอร์ (ลดความเสี่ยงเวลาอินเทอร์เน็ตมีปัญหา)
* **Break-even Protection:** ระบบเลื่อน Stop Loss มาบังหน้าทุนอัตโนมัติ เมื่อราคาวิ่งไปในทิศทางที่ถูกต้องจนถึงระยะที่กำหนด
* **State Persistence:** จำสถานะออเดอร์ได้แม้ Server รีสตาร์ท โดยซิงค์ข้อมูลกับ Supabase Real-time
* **Telegram Alerts:** แจ้งเตือนทุกจังหวะสำคัญ (สัญญาณมา, เปิดออเดอร์, เลื่อน SL, ปิดกำไร/ขาดทุน, และแจ้งเตือนเมื่อเกิด Error)

---

## 🛠️ สิ่งที่ต้องเตรียม (Prerequisites)

ก่อนติดตั้งและรันบอท ตรวจสอบให้แน่ใจว่าคุณมีสิ่งเหล่านี้:

1. **Python 3.11** ขึ้นไป
2. **Deriv Account:** App ID และ API Token (ต้องมีสิทธิ์ Read และ Trade)
3. **Telegram Bot:** Bot Token จาก BotFather และ Chat ID ของคุณ
4. **Supabase Account:** สำหรับสร้างฐานข้อมูลเก็บบันทึกสถานะบอท

---

## ⚙️ การตั้งค่าสภาพแวดล้อม (Configuration)

สร้างไฟล์ `.env` ไว้ที่โฟลเดอร์ root ของโปรเจกต์ และกำหนดค่าตัวแปรดังต่อไปนี้:

```env
DERIV_APP_ID="ไอดีแอป Deriv ของคุณ"
DERIV_TOKEN="API Token จาก Deriv"
SUPABASE_URL="URL ของโปรเจกต์ Supabase"
SUPABASE_KEY="API Key (anon/public) ของ Supabase"
TELEGRAM_TOKEN="Token ของ Telegram Bot"
TELEGRAM_CHAT_ID="Chat ID ของคุณ"
