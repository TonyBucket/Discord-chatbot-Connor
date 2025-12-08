# English here! [![English](https://img.shields.io/badge/lang-English-green)](README_EN.md)
# 🤖 Discord Chatbot (based trên source code của Connor) 
*(phiên bản “plug-n-play”, đọc xong chạy liền, lỗi ráng chịu 😭)*

Một con chatbot Discord AI đơn giản – nhưng không đơn điệu –  
biết stream tin nhắn như hacker phim Mỹ, nhớ ngắn hạn như cá 7s,  
và trả lời mention/keyword cực mượt.

Chỉ cần sửa đúng **1 file config**, quăng token vô `.env`,  
rồi chạy **python bot.py** là done, đi nhậu tiếp 🍻.

---

## ✨ Tính năng
- 🫵 Mention bot hoặc keyword → bot rep ngay  
- 🧠 Nhớ được vài dòng chat gần nhất  
- 💨 Stream reply theo kiểu typing ảo  
- 🖼️ Nhận ảnh (image_url) mượt  
- 🎮 Xoay status từ `games.json`  
- 🧽 Slash command `/purgememory` để reset khi bot lú

> Đây là bản Việt hoá funni.  
> Bug phát sinh = *“tự chịu trách nhiệm trước bàn phím của bạn”* 🦭

---

## ⚙️ Setup (dễ hơn 5 bước gọt mít)
### 1. Cài dependency
```bash
pip install -r requirements.txt
```

### 2. Tạo file `.env`
Copy `.env.example` thành `.env`, rồi điền:
```env
DISCORD_TOKEN=token_bot_cua_ban
OPENAI_API_KEY=key_openai_cua_ban
```

Có thể override thêm (không bắt buộc):
```env
MODEL_NAME=
API_ENDPOINT=
TRIGGER_KEYWORDS=
ADMIN_IDS=
```

*(điền sai thì bot tự tin chết, đừng hỏi 😭)*

### 3. Chỉnh `config.json`
- `trigger_keywords`: từ khoá bot tự rep không cần ping **(ĐÂY LA BẮT BUỘC, NẾU KHÔNG BOT SẼ REPLY BẤT KÌ TIN NHẮN NÀO)**
- `admin_ids`: ID mấy ông nội được phép xoá memory người khác  
- `model_name`: model OpenAI  
- `api_endpoint`: endpoint API (dùng local LLM vẫn được)

### 4. Chỉnh prompt bot
Từ giờ **bot dùng file** `system_prompt.txt` để mô tả tính cách.
Chỉ cần tạo file:
```bash
system_prompt.txt
```
và viết prompt vào đó, ví dụ:
```txt
Bạn tên là connor. TonyBucket là bố của bạn
36 36 36 36 36 36 36 36 36 36 36 36 36
67
```
Nếu **không có file này**, bot sẽ fallback sang:
- `SYSTEM_PROMPT` trong `.env`
- hoặc `"system_prompt"` trong `config.json`

### 5. Chạy bot
```bash
python bot.py
```

Nếu bot im ru → check `.env`.  
Nếu bot rep loạn → blame model, đừng blame t.

---

## 📁 File trong repo
- **bot.py** – linh hồn của bot  
- **config.json** – nơi chỉnh behavior
- **system_prompt.txt** – prompt riêng của bot (ưu tiên cao nhất)
- **.env.example** – template token/API  
- **games.json** – list status  
- **chat_memory.json** – memory 6 dòng  
- **requirements.txt** – dependency  
- **README.md** – file m đang đọc

---

## ⚠️ Ghi chú cuối
Bot này hoạt động theo triết lý:

> **“If it works, don’t touch it.  
> If it breaks, it’s your fault.”**

Dùng để vui, không dùng để cứu thế giới AI 🌏  
Chúc mấy tml xài bot vui vẻ 🦭💙
