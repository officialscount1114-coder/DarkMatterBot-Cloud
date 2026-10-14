# 🚂 Railway.app Deployment Guide (বাংলা & English)

DarkMatterBot is fully optimized for **Railway.app** deployment. You can run the **Relay Server**, **Slave Worker Nodes**, and **Telegram Bot Controller** 24/7 on Railway's high-speed cloud infrastructure.

---

## 🇧🇩 বাংলা নির্দেশিকা (Step-by-Step Railway Setup)

### ১. Railway তে ডিপ্লয় করার ধাপসমূহ:

1. **GitHub Repository তৈরি করুন:**
   - এই প্রজেক্ট ফোল্ডারটি (`DarkMatterBot-FULL`) আপনার GitHub একাউন্টে একটি নতুন Repository তে পুশ (Push) করুন।

2. **Railway.app এ লগইন করুন:**
   - [Railway.app](https://railway.app) এ যান এবং আপনার GitHub একাউন্ট দিয়ে লগইন করুন।
   - **New Project** > **Deploy from GitHub repo** সিলেক্ট করুন এবং আপনার রিপোজিটরি সিলেক্ট করুন।

3. **Environment Variables সেট করুন (Variables Tab):**
   Railway ড্যাশবোর্ডে গিয়ে **Variables** ট্যাবে নিচের ভেরিয়েবলগুলো যুক্ত করুন:
   - `DM_SECRET_KEY`: `your-32-character-secret-key-here-123456` *(কমপক্ষে ৩২ অক্ষরের একটি পাসওয়ার্ড/সিক্রেট)*
   - `DM_LOG_LEVEL`: `INFO` *(অপশনাল)*
   - `DM_TELEGRAM_BOT_TOKEN`: `your-telegram-bot-token` *(যদি টেলিগ্রাম দিয়ে কন্ট্রোল করতে চান)*
   - `DM_TELEGRAM_CHAT_ID`: `your-telegram-chat-id` *(অপশনাল)*

4. **ডিপ্লয়মেন্ট ও ডোমেন জেনারেট:**
   - Railway স্বয়ংক্রিয়ভাবে `Dockerfile` ও `railway.json` দিয়ে প্রজেক্ট বিল্ড করবে।
   - **Settings** ট্যাবে যান > **Networking** সেকশনে **Generate Domain** এ ক্লিক করুন (যেমন: `darkmatter-production.up.railway.app`)।

5. **ওয়েব ড্যাশবোর্ড চেক করুন:**
   - আপনার জেনারেট হওয়া ডোমেনে ব্রাউজার দিয়ে ভিজিট করুন (`https://your-domain.up.railway.app/`)।
   - আপনি লাইভ সাইবারপাঙ্ক **DarkMatter Cloud Relay Dashboard** দেখতে পাবেন এবং `/health` এন্ডপয়েন্টে সবুজ 200 OK পাবেন।

6. **ডেস্কটপ অ্যাপের সাথে কানেক্ট করুন:**
   - আপনার পিসিতে `main.py` ওপেন করুন।
   - **Master Control** ট্যাবে যান > **Relay Mode** সিলেক্ট করুন।
   - **Relay Host**: `your-domain.up.railway.app`
   - **Relay Port**: `443` (বা `80`)
   - **Secret Key**: আপনার দেওয়া `DM_SECRET_KEY`
   - **Connect** এ ক্লিক করুন!

---

## 🇺🇸 English Guide

### Deploying Relay Server to Railway

1. Push this codebase to your GitHub repository.
2. In Railway, click **New Project** -> **Deploy from GitHub repo**.
3. Railway automatically detects `Dockerfile`, `railway.json`, and `Procfile`.
4. In Railway project settings, go to **Variables** and add:
   ```env
   DM_SECRET_KEY=darkmatter_32_character_secret_key_prod_12345
   DM_LOG_LEVEL=INFO
   ```
5. In **Settings -> Networking**, click **Generate Domain**.
6. Visit `https://your-app.up.railway.app/` to open the live Web Status Dashboard.

### Health Check Endpoints:
- `GET /` -> Real-time Cluster Web Dashboard
- `GET /health` -> Railway Health Check JSON (`{"status": "ok"}`)
- `GET /api/status` -> Live JSON metrics (CPU/RAM/Slaves)
- `GET /api/agents` -> Connected Slaves telemetry
- `WS /ws` -> HMAC-SHA256 Authenticated WebSocket Endpoint

### Connecting Slave Workers:
Run on any VPS or Docker container to connect to your Railway Relay:
```bash
python slave.py --mode relay --master your-app.up.railway.app:443 --secret "your-32-char-secret" --name slave-vps-01
```
