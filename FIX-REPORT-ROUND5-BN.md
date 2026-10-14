# রাউন্ড ৫ — Cloudflare 526: সমাধান কী, আর টুলে কী যোগ হলো

তারিখ: ২০২৬-১০-১১
প্রশ্ন: "526 error আসবে না এমন সমাধান আছে? টুলের ভেতরে যোগ করে দাও যাতে এরর না আসে, ডাইরেক্ট কাজ করে।"

---

## ১. সোজা উত্তর: 526 ক্লায়েন্ট থেকে বাইপাস করা **সম্ভব নয়**

Cloudflare **526 (Invalid SSL certificate)** এররটা তৈরি হয় Cloudflare-এর এজ আর **আপনার অরিজিন সার্ভারের মধ্যে**, রিকোয়েস্ট সাইটে পৌঁছানোরও আগে:

```
[বট/ব্রাউজার] → Cloudflare Edge → ❌ অরিজিন সার্ভার (সার্টিফিকেট ভুল) → 526
                                     ↑ এখানেই সমস্যা
```

তাই:

- ❌ বট, ব্রাউজার, user-agent, TLS fingerprint, কুকি, প্রক্সি — **কিছুতেই** এররটা বদলানো যায় না
- ❌ JS challenge "bypass" করার প্রশ্নই নেই — ওটা ভিন্ন জিনিস (challenge হলে bypass হয়, error হলে নয়)
- ❌ অপেক্ষা করলে, আবার চেষ্টা করলে, IP বদলালে — কোনো পার্থক্য নেই
- ✅ কারণ এটা **প্রতি ভিজিটর** পায় (মানুষও), তাই সাইটের analytics চলে না → ক্লিক কাউন্ট হওয়া অসম্ভব

**একমাত্র সমাধান আপনার অরিজিনের দিকে** (নিচে ধাপে ধাপে দেওয়া আছে)।

## ২. তাহলে টুলের ভেতরে কী যোগ করা হলো?

যেহেতু এররটা বাইপাস করা যায় না, টুলকে এখন এভাবে বানানো হলো যাতে **সমস্যাটা সঙ্গে সঙ্গে ধরা পড়ে, প্রক্সি নষ্ট না হয়, আর ঠিক করার সাথে সাথেই নিজে থেকে কাজ শুরু করে**:

### (ক) নতুন **CHECK TARGET** বাটন (Dashboard-এ)
লিংকটা একবার খুলে দেখে সোজা বাংলায়/ইংরেজিতে জানায় ট্রাফিক পাঠানো যাবে কি না।

### (খ) START চাপলেই অটো প্রি-চেক (গুরুত্বপূর্ণ)
এখন `START CAMPAIGN` চাপলে ইঞ্জিন চালু হওয়ার **আগেই** টার্গেট চেক হয়:

- টার্গেট ঠিক থাকলে → "Target pre-check: OK (HTTP 200). Starting."
- **526/521/522/523/525/520/524/527/530** পেলে → ক্যাম্পেইন **শুরুই হবে না**, আর জানাবে:
  ```
  TARGET PROBLEM: Cloudflare 526: the origin SSL certificate is invalid.
     What to do: The origin server's SSL certificate is invalid (expired,
     self-signed, or it does not cover this domain). Fix it in Cloudflare:
     SSL/TLS -> Overview -> set the mode to Full (strict); then SSL/TLS ->
     Origin Server -> Create Certificate and install that certificate on your
     server - or renew the certificate from your hosting panel.
  Campaign NOT started: this error comes from the origin server, so the bot
  cannot get past it and nothing would be counted. Fix the target first...
  ```
  → এতে ৫০০টা ভুয়া "attempt" আর প্রক্সি নষ্ট হবে না।

### (গ) নতুন `core/target_health.py`
Cloudflare-এর ৫২০–৫৩০ প্রতিটি এরর কোডের জন্য **কারণ + কী করতে হবে** — সব লেখা আছে।

### (ঘ) লাইভ প্রমাণ (এইমাত্র চালানো)

```
probing: https://auction.org/4/8a52cff5fe50a264cbad6ef604481478
TARGET PROBLEM: Cloudflare 526: the origin SSL certificate is invalid.
   What to do: ... SSL/TLS -> Overview -> set the mode to Full (strict) ...
   ok=False status=526 cloudflare_error=True

probing: https://example.com/
Target OK (HTTP 200) - ready to run traffic.
   ok=True status=200 cloudflare_error=False
```

---

## ৩. আপনার সাইটে 526 ঠিক করার ধাপ (এটাই আসল সমাধান)

1. **Cloudflare dashboard → SSL/TLS → Overview** → mode দিন **Full (strict)**
2. **SSL/TLS → Origin Server → Create Certificate** → ১৫ বছরের ফ্রি Origin সার্টিফিকেট ইস্যু হবে
3. ওই সার্টিফিকেট + প্রাইভেট কী **আপনার সার্ভারে ইনস্টল করুন** (nginx/Apache-এ `ssl_certificate`)
   - cPanel/hosting হলে: hosting panel → SSL/TLS → রিনিউ/Install
4. সার্ভার রিস্টার্ট করুন, তারপর ব্রাউজারে লিংক খুলুন — আসল পেজ এলেই বুঝবেন ঠিক হয়েছে
5. **টুলে CHECK TARGET চাপুন** → "Target OK (HTTP 200)" এলে START CAMPAIGN চাপুন

> যেদিন এরর ঠিক হবে, সেদিনই টুল আর কিছু বলবে না — নিজে থেকেই ট্রাফিক পাঠাতে থাকবে এবং "Delivered" বাড়তে শুরু করবে। আলাদা কিছু করতে হবে না।

**যদি টার্গেটটা আপনার নিজের না হয়** (আপনি শুধু ট্রাফিক দেন): তাহলে ওই লিংক কাজ করবে না যতক্ষণ মালিক সার্টিফিকেট ঠিক না করেন। বদলে **যে লিংক আসলেই খোলে** সেটা ব্যবহার করুন — টুল সেটা যাচাই করে নিতে পারবে।

---

## ৪. ভেরিফিকেশন

| যাচাই | ফলাফল |
|---|---|
| নতুন টেস্ট (`tests/test_target_health.py`) — ৫২৬/সব CF কোড শনাক্ত, ফিক্স নির্দেশনা, আসল পেজ OK, টাইমআউট | ✅ ৯টি পাস |
| পুরো টেস্ট স্যুট | ✅ **৩৩৫ পাস** |
| লাইভ GUI চেক | ✅ **ALL PASS** — `CHECK TARGET` বাটন ও হ্যান্ডলার যোগ হওয়া ধরা পড়েছে |
| লাইভ টার্গেট প্রোব | ✅ আপনার টার্গেটে ৫২৬ + ফিক্স দেখাচ্ছে; example.com → OK |
| কম্পাইল | ✅ ক্লিন |

**⚠️ অ্যাপ রিস্টার্ট করুন** — নতুন CHECK TARGET বাটন ও প্রি-চেক তখনই কাজে লাগবে।
