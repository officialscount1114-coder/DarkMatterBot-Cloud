# DarkMatterBot — লাইভ চেক রিপোর্ট (রাউন্ড ২)

তারিখ: ২০২৬-১০-১১
পদ্ধতি: টুলটা **সত্যিই চালিয়ে** দেখা হয়েছে (Python 3.13 — যে ইন্টারপ্রেটারে আপনার সব ডিপেন্ডেন্সি আছে), শুধু কোড পড়ে নয়।

সারসংক্ষেপ: ৫টি নতুন ত্রুটি পাওয়া গেছে ও ঠিক করা হয়েছে — **সবচেয়ে বড়টা হলো ডিপ্লয়ে করা স্লেভ শুরুই হতে পারত না**, তাই মাস্টার/স্লেভ মোডে ট্রাফিক কখনোই আসত না ("সে পারে না")।

---

## ১. 🔴 CRITICAL — ডিপ্লয়ে করা স্লেভ চালু হতই না

**লাইভ প্রমাণ** (ঠিক করার আগে, `server_deploy` ফোল্ডারটা ডিপ্লয়ের মতো চালিয়ে):

```
File "slave.py", line 42, in <module>
    from ui.utils import Utils
ModuleNotFoundError: No module named 'ui'
```

**কারণ:** `package_server.py` যে সার্ভার-প্যাকেজ বানায়, তাতে `ui/` ফোল্ডার **যোগ করাই হয় না** — অথচ `slave.py` সেটা import করে। সার্ভারে `sudo systemctl start dm-slave` চালালে স্লেভ সাথে সাথে ক্র্যাশ করত → মাস্টার "Start Traffic" দিলেও কোনও স্লেভ ট্রাফিক পাঠাত না। এটাই "traffic আসে না" এর মূল কারণ।

### সাথে সম্পর্কিত আরও দুটো প্যাকেজিং ত্রুটি

`package_server.py`-এ `core/` মডিউলগুলোর একটা **হাতে লেখা লিস্ট** ছিল, সেটা কোডের সাথে মিলছিল না। যেগুলো বাদ পড়েছিল কিন্তু অন্য মডিউল import করে:

| হারানো মডিউল | কে import করে | ফলাফল |
|---|---|---|
| `paths.py` | `constants`, `header_manager`, `settings_keys`, `session_manager`, `source_health_tracker` | ImportError |
| `session_manager.py` | `engine`, `browser_engine` | ImportError |
| `source_health_tracker.py` | `proxy_manager` | ImportError |
| `engine_factory.py`, `settings_keys.py` | `config_builder`, `browser_engine` | ImportError |

### সমাধান
- `package_server.py` এখন **পুরো `core/` ফোল্ডার** প্যাক করে (হাতে লিস্ট আর নেই — তাই ভবিষ্যতে নতুন মডিউল যোগ করলে বাদ পড়বে না)।
- `ui/utils.py` (শুধু সেটাই দরকার — বাকি `ui/` tkinter চায়) আর একটা **হালকা `ui/__init__.py`** (কোনো GUI import নেই) বান্ডেলে যোগ করা হয়েছে।
- `server_deploy/` ফোল্ডারটাও একইভাবে ঠিক করা হয়েছে, যাতে সরাসরি কপি করলেও কাজ করে।

**লাইভ প্রমাণ (ঠিক করার পরে)** — `server_deploy.zip` খুলে আসল `slave.py` চালানো:
```
INFO | dm-slave | DarkMatter Slave Node Starting
INFO | dm-slave | Slave Name: zip-slave
INFO | core.slave_controller | Starting slave controller: zip-slave (direct mode)
INFO | core.websocket_client | Connecting to master at ws://127.0.0.1:65500/ws
```
আর আসল মাস্টারের সাথে: স্লেভ কানেক্ট করল, ট্রাফিক কমান্ড নিল, টার্গেটে হিট গেল, আর স্ট্যাটস ফেরত পাঠাল — সবই নিচে §৪-এ।

---

## ২. 🟠 STOP চাপার পরেও ৫ সেকেন্ড ট্রাফিক চলত

**লাইভ মাপা** (`core/slave_controller.py`): STOP পাঠানোর পরও ২৫টি বাড়তি হিট গেছে, প্রায় **৪.৫ সেকেন্ড** ধরে:

```
hits before stop: 42
    0.5s  46      2.0s  55      4.0s  65
    1.0s  49      3.0s  60      4.5s  67  <- এখানে থামল
```

**কারণ:** অপারেশন লুপ `await asyncio.sleep(self._stats_interval)` — ডিফল্ট **৫ সেকেন্ড** — এ ঘুমিয়ে থাকত, আর স্টপ ইভেন্ট চেক করত শুধু ঘুম থেকে উঠে। তাই "Stop" চাপার পরেও প্রায় ৫ সেকেন্ড ধরে রিকোয়েস্ট যেত।

**সমাধান:** ঘুমের বদলে স্টপ-ইভেন্টে অপেক্ষা করা হয় (`asyncio.wait_for(event.wait(), timeout=stats_interval)`) — STOP এলে সাথে সাথে জাগে।

**লাইভ প্রমাণ (ঠিক করার পরে)** — বাড়তি হিট **০**:
```
hits at stop call : 43
hits 2s after stop: 43
hits at end (12s) : 43
extra hits after 2s: 0     VERDICT: traffic stops promptly
```

---

## ৩. 🟠 প্রক্সি লিস্ট হাই-DPI স্ক্রিনে চেপে যেত (২-৪ সারি)

`ui/pages/proxy_manager.py::_on_page_resize` পেজের **৬৬%** টুল-কার্ডকে দিত, এবং সেটা হিসাব করত **আসল পিক্সেল** দিয়ে — কিন্তু সেই সংখ্যাটা customtkinter আবার DPI ফ্যাক্টর দিয়ে গুণ করে। তাই স্কেলড স্ক্রিনে জায়গার হিসাব দুইবার ভুল হয়ে যেত:

| উইন্ডো / DPI | আগে visible rows | এখন |
|---|---|---|
| 1160x780 @100% | ৫ | **৭** |
| 900x600 @100% | ৩ | **৭** |
| 860x560 (সর্বনিম্ন) | ২ | ৫ |
| 1450x975 @125% | ৫ | **৭** |
| 1740x1061 @150% | ৩ | **৭** |

**সমাধান:** হিসাব এখন CTk ইউনিটে হয় (`logical_height = height / factor`), আর প্রক্সি গ্রিডের জন্য নির্দিষ্ট জায়গা রেখে বাকিটা টুল-কার্ডকে দেওয়া হয় (কার্ডগুলো scrollable, তাই ছোট হলে সমস্যা নেই)। ফলে **প্রতিটি DPI-তে একই সংখ্যক সারি** দেখা যায়।

---

## ৪. লাইভ এন্ড-টু-এন্ড যাচাই — আসল মাস্টার + আসল স্লেভ + আসল টার্গেট

আসল `MasterServer` + সাবপ্রসেসে আসল `slave.py` (ডিপ্লয় করা কপি) + লোকাল HTTP টার্গেট:

| যাচাই | ফলাফল |
|---|---|
| প্যাকেজ করা স্লেভ প্রসেস বেঁচে থাকে | ✅ PASS |
| স্লেভ মাস্টারে কানেক্ট ও অথেন্টিকেট করে | ✅ slave_count=1 |
| মাস্টার ট্রাফিক কমান্ড পাঠায় | ✅ 1 slave accepted |
| **ট্রাফিক সত্যিই টার্গেটে পৌঁছায়** | ✅ hits received |
| স্লেভ স্ট্যাটস মাস্টারকে ফেরত পাঠায় | ✅ master sees `('live-slave', 24, 23, 'traffic')` |
| STOP করার পর ট্রাফিক থামে | ✅ extra hits: 0 |
| প্রক্সি ছাড়া + direct blocked → পাঠায় না | ✅ 0 hits + কারণ লগ |

স্লেভের নিজের লগ (Tick-এর সঠিকতা প্রমাণ):
```
Fast engine started (continuous). 0 proxies, 3 threads.
Fast engine stopped. 27 visits delivered, 0 failed out of 27 attempts (100% delivery rate).
[→Master] Traffic complete: 27 success, 0 failed
[→Master] No proxies available and direct traffic is disabled: this slave would send
          every visit from its own IP, so the traffic was not started.
```

---

## ৫. GUI লাইভ চেক (আসল উইন্ডো খুলে)

GUI আসলেই খোলা হয়েছে ও প্রতিটি পেজ বানানো/মাপা হয়েছে:

| যাচাই | ফলাফল |
|---|---|
| অ্যাপ বিল্ড + ৫টি পেজ (run/proxy/stress/master/settings) | ✅ সব 922x682, mapped |
| হেডার, page host, activity log | ✅ সব mapped ও সঠিক সাইজ |
| ৫টি স্ট্যাট কার্ড (Attempts/Delivered/Failed/Delivery/Proxies) | ✅ আছে |
| Delivery % হিসাব (100 attempt / 12 delivered → 12%) | ✅ সঠিক |
| `ui.scaling` ফ্যাক্টর == customtkinter-এর ফ্যাক্টর | ✅ হুবহু মেলে |
| গ্রিডের row height DPI অনুযায়ী স্কেল | ✅ (150%-এ 45px) |
| কলাম হেডার বনাম ক্যানভাস কলাম ড্রিফট | ✅ ০.০ px |
| ড্যাশবোর্ডে collapsed/কাটা widget | ✅ একটাও নেই |
| ঝিকমিক / unmapped হয়ে যাওয়া (flicker টেস্ট, ৯৫টি স্যাম্পল) | ✅ flips = 0 |

---

## ৬. এই রাউন্ডে যা পরিবর্তন হয়েছে

| ফাইল | পরিবর্তন |
|---|---|
| `package_server.py` | পুরো `core/` প্যাক করে; `ui/utils.py` + হালকা `ui/__init__.py` যোগ |
| `server_deploy/core/*` | সব কোর মডিউল সিঙ্ক (27টি) |
| `server_deploy/ui/` | নতুন: `__init__.py` (GUI-free stub) + `utils.py` |
| `core/slave_controller.py` | STOP-এ সাথে সাথে জাগে (stats_interval ঘুম বাদ) |
| `ui/pages/proxy_manager.py` | পেজ ভাগ DPI-aware; গ্রিড এখন reliable ৭ সারি |

আগের রাউন্ডের ১৭টি ফিক্স অপরিবর্তিত আছে।

**টেস্ট:** ৩১০টি টেস্ট পাস · সব `.py` কম্পাইল ক্লিন · উপরের সব লাইভ চেক পাস।

---

## ৭. যা এখনো যাচাই করা হয়নি

- **আসল প্রক্সি দিয়ে আসল টার্গেটে ট্রাফিক** — এখান থেকে আপনার টার্গেটে প্রক্সি দিয়ে ট্রাফিক পাঠানো হয়নি; লোকাল টার্গেট + direct মোডে প্রমাণ করা হয়েছে যে চেইন কাজ করে। আসল ফলাফল আপনার প্রক্সির মানের উপর নির্ভরশীল।
- **Linux সার্ভারে systemd দিয়ে ডিপ্লয়** — কোড-লেভেলে ঠিক করা ও Windows-এ ডিপ্লয়-সিমুলেশন করে যাচাই করা হয়েছে, কিন্তু আসল Linux বক্সে `sudo bash deploy/install.sh` চালানো হয়নি।
- **captcha solver / Cloudflare bypass** — এর জন্য বাইরের সার্ভিস/API key লাগে, টেস্ট করা হয়নি।

## ৮. এখন যা করবেন

1. **লোকাল (একই মেশিনে) চালাতে:** `RUN.bat` → Proxy Manager-এ প্রক্সি চেক → Dashboard-এ ক্রুসেইড।
2. **মাস্টার/স্লেভ চালাতে:** `server_deploy.zip` **নতুন করে বানান** (`python package_server.py`) অথবা এই ফোল্ডারের `server_deploy/` কপি করুন — পুরনো zip-টা ব্যবহার করবেন না, সেটাই ভাঙা ছিল।
3. স্লেভে `.env`-এ `DM_MASTER_HOST` / `DM_SECRET_KEY` / `DM_SLAVE_NAME` দিন → `sudo systemctl start dm-slave`.
4. মাস্টারে স্লেভ connect দেখার পর: Scrape → Check (এখন স্লেভ থেকে পাওয়া কাজ করা প্রক্সিগুলো মাস্টার ফেরত পায়) → Start Traffic.
