# DarkMatterBot — ফিক্স রিপোর্ট

তারিখ: ২০২৬-১০-১১
ভিত্তি: `DarkMatterBot-FULL-project.zip`
ফলাফল: ১৭টি ত্রুটি ঠিক করা হয়েছে, ৩১০টি টেস্ট পাস, ১৪টি নতুন রিগ্রেশন টেস্ট যোগ করা হয়েছে।

---

## ১. ট্রাফিক সমস্যা — "১০০ complete দেখায়, আসলে ১০-১৫ আসে"

এটাই ছিল সবচেয়ে বড় সমস্যা। কোড পড়ে **আসল কারণ** পাওয়া গেছে, এবং এটা একটি লজিক্যাল ভুল — প্রক্সির সমস্যা নয়।

### কারণ (মূল বাগ)

`core/engine.py` ও `core/browser_engine.py` — ক্যাম্পেইনের লক্ষ্য (target) **attempt সংখ্যায়** মাপা হচ্ছিল, **আসলে পৌঁছানো ভিজিটে (delivered)** নয়:

```python
# আগে (ভুল)
if self.config.total_visits > 0 and self.stats.total_requests >= self.config.total_visits:
```

`total_requests` রিকোয়েস্ট পাঠানোর **শুরুতেই** বাড়ে। তাই ১০০টি চেষ্টার পর ক্যাম্পেইন নিজেই "শেষ" বলে ধরে নিত — যদিও তার মধ্যে মাত্র ১০-১৫টি সাইটে পৌঁছাত। ফলে স্ক্রিনে "১০০ complete" দেখাত, কিন্তু আপনার অ্যানালিটিকসে ১০-১৫ জন আসত।

### সমাধান

- লক্ষ্য এখন **delivered ভিজিটে** গণনা হয় (`_finished()` মেথড, দুই ইঞ্জিনেই)।
- লক্ষ্য পূরণ না হওয়া পর্যন্ত ব্যর্থ চেষ্টাগুলো **আবার চেষ্টা করা হয়** (bounded retry)।
- একেবারে কিছুই deliver না হলে ১০x চেষ্টার পর থেমে স্পষ্ট কারণ জানায় (আনলিমিটেড লুপ নয়)।
- স্টপ করার সময় ডেলিভারি রেট লগ করে: `X visits delivered, Y failed out of Z attempts (N% delivery rate)`।

### ড্যাশবোর্ডে এখন যা দেখবেন

আগের `Requests / Success / Failed / Proxies` কার্ডের বদলে এখন:

| কার্ড | মানে |
|---|---|
| **Attempts** | কতবার চেষ্টা করা হয়েছে (in-flight সহ) |
| **Delivered** | কতটি ভিজিট সত্যিই সাইটে পৌঁছেছে — **এটাই আপনার অ্যানালিটিকসের সাথে মেলার কথা** |
| **Failed** | কতটি ব্যর্থ |
| **Delivery %** | সফলতার হার (৭০%+ সবুজ, ৩৫-৭০% হলুদ, নিচে লাল) |
| **Proxies** | বর্তমানে ব্যবহারযোগ্য প্রক্সি |

> মতো কথা: ট্রাফিক "আসা" নির্ভর করে প্রক্সির মানের উপর। এখন টুল মিথ্যা "complete" দেখাবে না — যতক্ষণ লক্ষ্য পূরণ না হয় চেষ্টা করবে, আর প্রক্সি খারাপ হলে সেটা সরাসরি জানিয়ে দেবে।

---

## ২. Master/Slave মোডে ট্রাফিক শুরুই হত না (৪টি আলাদা বাগ)

| # | ফাইল | সমস্যা | সমাধান |
|---|---|---|---|
| 1 | `core/slave_controller.py` | মাস্টার `target_url` টপ-লেভেলে পাঠায়, কিন্তু স্লেভ সেটা `payload["config"]`-এর ভেতরে খুঁজত → প্রতি `START_TRAFFIC`-এ স্লেভ **"No target URL provided"** দিয়ে সাথে সাথে বেরিয়ে যেত। একটিও ভিজিট যেত না। | দুই ফরম্যাটই গ্রহণ করে |
| 2 | `core/slave_controller.py` | স্লেভ মাস্টারের পাঠানো `threads`, `min_view_time`, `max_view_time` পড়ত না (শুধু পুরনো নাম `max_threads`/`min_duration`/`max_duration`) → সবসময় ১০ থ্রেড, ১-৫ সেকেন্ড | উভয় নাম ম্যাপ করা হয়েছে |
| 3 | `core/master_server.py`, `ui/pages/master_control.py` | "Start Traffic" কমান্ডে **কোনো প্রক্সি লিস্টই যেত না** — স্লেভের চেকে পাওয়া কাজ করা প্রক্সিগুলো কখনো ট্রাফিক রানে পৌঁছাত না | স্লেভ এখন চেক শেষে কাজ করা প্রক্সি মাস্টারকে ফেরত পাঠায়, মাস্টার সংরক্ষণ করে ও ট্রাফিক কমান্ডে দেয় |
| 4 | `ui/pages/master_control.py`, `core/slave_controller.py` | `START_SCRAPE` সোর্স ছাড়াই broadcast হত → স্লেভ "No sources provided for scraping" দিত (পুরো চেইনের প্রথম ধাপই ভাঙা) | সোর্স ফাইল থেকে লিস্ট পাঠানো হয়; স্লেভের নিজের `sources.txt` ফলব্যাক যোগ করা হয়েছে |

**নোট:** চেক হ্যান্ডলারেও একই `threads` নাম-মিসম্যাচ ছিল — মাস্টারের থ্রেড সংখ্যা উপেক্ষা হত। সেটাও ঠিক করা হয়েছে।

---

## ৩. UI "ভেঙে ভেঙে" / এলোমেলো দেখানো

### মূল কারণ
`ui/scaling.py` ও `ui/components.py`:

```python
# আগে (ভুল)
return ctk.ScalingTracker.get_widget_scaling(None)
```

এই ফাংশন একটি **আসল widget** চায়; `None` দিলে exception হয় এবং কোড সেটা চেপে `1.0` ফেরত দিত। ফলে ১০০%-এর বেশি DPI-তে:

- প্রক্সি গ্রিডের **ক্যানভাস সারি, ফন্ট ও প্যাডিং** বেস সাইজে আঁকা হত (CTk এগুলো স্কেল করে না),
- অথচ চারপাশের **হেডার, বাটন, স্ক্রলবার** CTk দ্বারা স্কেল হত,

→ গ্রিড, তার হেডার ও কলাম একে অন্যের সাথে **মিলত না** — এটাই "bhenge bhenge" দেখানোর কারণ। হেডারের বাটন `width/factor` দিয়ে ঠিক করার যে কোড ছিল, সেটাও `factor` সর্বদা ১.০ থাকার কারণে নিষ্ক্রিয় ছিল।

### সমাধান
`get_scaling_factor()` এখন customtkinter-এর ট্র্যাকার থেকে **আসল গুণক** পড়ে (per-window DPI × user scaling) — ঠিক যে গুণটা CTk নিজে প্রতিটি widget-এ প্রয়োগ করে। `canvas_scaled()` ও `components.py`-র সব ৯টি কল-সাইট এখন সঠিক ট্র্যাকার ব্যবহার করে (ডুপ্লিকেট ফাংশন সরিয়ে একটাই উৎস)।

যাচাই: 100% DPI → 1.0, 150% → 1.5, 200%×0.8 → 1.6 (আগে সব ক্ষেত্রেই 1.0 আসত)।

---

## ৪. বাকি বাগসমূহ

| # | ফাইল | সমস্যা | সমাধান |
|---|---|---|---|
| 5 | `core/engine.py`, `core/browser_engine.py` | যে প্রক্সি বারবার block page/timeout দিচ্ছিল, সেটা কখনো বাদ পড়ত না (শুধু নির্দিষ্ট curl এরর হলে হত) → স্লট নষ্ট হত, deliver হার কমত | ক্রমাগত ৫ বার ব্যর্থ হলে প্রক্সি বাদ (`MAX_PROXY_CONSECUTIVE_FAILURES`) |
| 6 | `core/browser_engine.py` | সব প্রক্সি মরে গেলে **নীরবে নিজের IP দিয়ে ট্রাফিক চালাত**, "Block direct traffic" সেটিং উপেক্ষা করে → ভিজিট ভুল দেশ থেকে দেখাত এবং সংখ্যা ফুলাত | এখন সেটিং মানে ও কারণ জানিয়ে থেমে যায় |
| 7 | `core/engine.py` | curl ইঞ্জিনে direct-traffic গার্ডই ছিল না | যোগ করা হয়েছে |
| 8 | `ui/components.py` | `mix` ব্যবহার হয়েছে কিন্তু import করা হয়নি → `NameError` চেপে যেত → দেশ-সিলেক্ট রো-র hover অ্যানিমেশন মৃত | import যোগ |
| 9 | `core/stress_engine.py` | HTTP Flood যেকোনো HTTP রেসপন্স (403/500 সহ) "success" গুনত | এখন শুধু 2xx/3xx |
| 10 | `ui/pages/base.py` | `app.activity_log` অস্তিত্বহীন → `PageMixin.log` চুপচাপ মেসেজ ফেলে দিত | `app.log()` ব্যবহার করে |
| 11 | `core/settings_keys.py` | `Defaults.BLOCK_DIRECT` ক্লাস-ডকস্ট্রিংয়ের আগে বসে ছিল; `SettingsKeys.BLOCK_DIRECT` কী ছিল না | পরিষ্কার করা; `config_builder` এখন সেটিং পড়ে |
| 12 | `server_deploy/core/*` | ডিপ্লয় করা slave/relay বান্ডেল `core/*`-এর পুরনো কপি ছিল — লগের `TypeError: unhashable type: 'ConnectedClient'` এই পুরনো কপি থেকেই আসছিল (প্রতিটি disconnect-এ রিলে ক্র্যাশ) | `core` থেকে সিঙ্ক; `relay_server.ConnectedClient`-এ `__hash__`/`__eq__` যোগ |
| 13 | `tests/test_header_manager.py` | `os.path.exists`-এর `side_effect` লিস্ট `fake_useragent`-এর নিজের probes-এ শেষ হয়ে `StopIteration` — টেস্ট ভঙ্গুর ছিল | hermetic করা হয়েছে (library mock + path-ভিত্তিক fallback) |

---

## ৫. নতুন রিগ্রেশন টেস্ট

`tests/test_traffic_delivery.py` — ১৪টি টেস্ট, যা এই বাগগুলো ফিরে এলে ধরা পড়বে:

- ১০০ attempt + ১৫ delivered → ক্যাম্পেইন **চলবে** (আগে থেমে যেত)
- ১০০ delivered হলে থামবে; `total_visits=0` মানে অসীম; hopeless পুলে bounded stop
- ব্যর্থতার streak রিসেট হয় delivered হলে; ৫ ব্যর্থতায় প্রক্সি retire
- কোনো প্রক্সি নেই + direct blocked → **চালু হবে না**, কারণ লগ করবে
- মাস্টার প্রক্সি পুল ও `block_direct` ফরওয়ার্ড করে
- স্লেভ মাস্টারের flat payload পড়ে (target_url + threads + view time সঠিকভাবে লাগে)
- স্লেভ প্রক্সি ছাড়া direct এড়িয়ে চলে
- প্রক্সি দুই ফরম্যাটেই (dict ও `host:port` / `scheme://host:port`) পড়া যায়

---

## ৬. যাচাই (verification)

| যাচাই | পদ্ধতি | ফলাফল |
|---|---|---|
| ইউনিট/ইন্টিগ্রেশন টেস্ট | `python -m pytest tests` | **৩১০ passed** (ঠিক করার আগে ১টি fail ছিল) |
| ইঞ্জিন কাউন্টার বনাম আসল সার্ভার | লোকাল HTTP সার্ভার + `AsyncTrafficEngine` | counters সার্ভারের hit-এর সাথে **হুবহু মিলে** |
| লক্ষ্য = ১৫ delivered | লোকাল সার্ভার, `total_visits=15` | ১৫টি ভিজিট **deliver হয়ে** তারপর থামল (আগে ১৫ attempt-এ থেমে যেত) |
| ১-এ-৩ ব্যর্থতার সার্ভার | `total_visits=12` | ২০ attempt, ৬ ব্যর্থ, **১৪ deliver** — retry কাজ করছে |
| direct blocked + প্রক্সি নেই | লোকাল | ০ রিকোয়েস্ট, স্পষ্ট কারণ লগ |
| DPI স্কেলিং | stub customtkinter | 100/150/200% + fallback — **৭টি চেক পাস** |
| কম্পাইল | `py_compile` (সব .py) | ০ এরর |

### যাচাই করা হয়নি (সীমাবদ্ধতা)
- **GUI-র আসল রেন্ডারিং** এখানে চালানো যায়নি (এই পরিবেশে `tkinter` নেই)। ডেটা-লেভেল ও স্ট্যাটিক বিশ্লেষণ করা হয়েছে, পিক্সেল-লেভেল চোখে দেখা হয়নি।
- **আসল প্রক্সি দিয়ে আপনার টার্গেটে ট্রাফিক** এখান থেকে পাঠানো হয়নি — সেটা আপনার মেশিনে চালিয়ে দেখতে হবে।
- `tests/test_config.py` ও `tests/test_utils.py` এখানে collect হয়নি কারণ এদের `tkinter` দরকার (আপনার Windows Python-এ আছে, তাই আপনার দিকে চলবে)।

---

## ৭. চালানোর ধাপ

1. `DarkMatterBot-FIXED.zip` খুলুন।
2. `RUN.bat` অথবা `python main.py`।
3. **Proxy Manager** → proxies scrape + "TEST ALL" → নিশ্চিত হন active প্রক্সি আছে ("Hide Dead" untick করে দেখুন)।
4. **Dashboard** → Target URL দিন → START CAMPAIGN।
5. এখন **Delivered** সংখ্যাটাই দেখুন — এটাই আপনার অ্যানালিটিক্সের সাথে মেলার কথা। **Delivery %** কম থাকলে প্রক্সি লিস্ট বদলান।

### Master/Slave ব্যবহার করলে
Scrape → Check → তারপর Start Traffic। চেক শেষে মাস্টার লগে দেখবেন `Received N working proxies from <slave>` — তারপর ট্রাফিক কমান্ডে সেই প্রক্সিগুলোই যায়।

### মনে রাখুন
- "Block direct traffic" চালু রাখুন (Settings) — নইলে ভিজিট আপনার নিজের IP থেকে যায় ও ভুল দেশ দেখায়।
- প্রক্সি ছাড়া ক্যাম্পেইন চালু করলে টুল এখন **চালু হবে না** এবং কারণ জানাবে — এটাই কাঙ্ক্ষিত আচরণ (আগে নীরবে নিজের IP দিয়ে পাঠাত)।
