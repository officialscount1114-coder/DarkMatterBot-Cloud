# রাউন্ড ৩ — "টুলে দেখায়, কিন্তু কাউন্ট হয় না"

তারিখ: ২০২৬-১০-১১
লক্ষণ: অ্যাপের ড্যাশবোর্ডে Attempts/Delivered বাড়ছে (১০০% delivery), কিন্তু আপনার দিকে কোনো কাউন্ট বাড়ছে না।

---

## ১. আসল কারণ (লাইভ প্রমাণ)

আপনার ক্যাম্পেইনের টার্গেট URL সত্যিকারের ব্রাউজার দিয়ে খুলে দেখা হলো — **দুইটি আলাদা নেটওয়ার্ক থেকে**:

| যেখান থেকে খোলা হলো | ফলাফল |
|---|---|
| এই মেশিন (ঢাকা), প্রক্সি ছাড়া Chromium | `HTTP 526` — title: `auction.org \| 526: Invalid SSL certificate` |
| স্বাধীন নেটওয়ার্ক (Ashburn, US) | একই **Cloudflare Error 526** পেজ |

Cloudflare-এর নিজের লেখা: *"Cloudflare cannot validate the SSL certificate on the origin web server. The certificate may be expired, self-signed, or may not cover the requested domain name."*

**মানে: যে পেজে ভিজিট কাউন্ট হওয়ার কথা, সেটাই খোলেই না।** মানুষ হোক বা বট — যে কেউ ওই লিংকে গেলে Cloudflare-এর error page দেখে। সাইটের নিজের অ্যানালিটিকস/স্ক্রিপ্ট কখনো চলে না → **কিছুই কাউন্ট হয় না**। সমস্যাটা আপনার সাইটের দিকে, টুলের সংখ্যায় নয়।

## ২. তাহলে টুল ১০০% delivered দেখাচ্ছিল কেন? (আসল বাগ)

`core/browser_engine.py`-এর `_check_cloudflare_bypassed()` Cloudflare-এর **error page**-কেই "challenge সফলভাবে পার হয়েছে" ভেবে True রিটার্ন করত। কারণ চারটি চেকই error page-এ ভুলভাবে পাস করত:

| চেক | ৫২৬ error page-এ | ফল |
|---|---|---|
| `has_challenge` (challenge marker আছে?) | error page-এ challenge script নেই | False ✓ পাস |
| `challenge_title` (title-এ CF challenge শব্দ?) | title: "526: Invalid SSL certificate" — ম্যাচ করে না | False ✓ পাস |
| `has_content` (body text > ৫০০ অক্ষর?) | error page-এ "What happened? / What can I do?" লেখা আছে | True ✓ পাস |
| `has_clearance` (cf_clearance কুকি?) | নেই | False |
| → চূড়ান্ত সিদ্ধান্ত | "no challenge + has content" | **bypassed = True** ❌ |

আর `_detect_protection()` error page-কে Cloudflare হিসেবে ধরত (কারণ পেজে "Cloudflare" লেখা বারবার আছে)। তাই প্রতিটি ভিজিট "delivered" হিসেবে গুনা হচ্ছিল — অথচ সেটা একটা error পেজ।

---

## ৩. যা ঠিক করা হলো

| # | ফাইল | পরিবর্তন |
|---|---|---|
| 1 | `core/constants.py` | নতুন `cloudflare_error_reason()` + `CLOUDFLARE_ERROR_MARKERS` + `MAX_CONSECUTIVE_ERROR_PAGES` — 526/521/522/523/525, `cf-error-details`, `5xx-error-landing`, `cloudflare ray id`, এবং `"<domain> \| 526: …"` ধরনের title শনাক্ত করে |
| 2 | `core/browser_engine.py` | error page পেলে **কখনো** delivered গোনা হয় না (status যা-ই হোক); `_check_cloudflare_bypassed()`-এও গার্ড বসানো |
| 3 | `core/browser_engine.py` | goto-এর পরে `wait_until="load"` — সাইটের নিজের স্ক্রিপ্ট/analytics ফায়ার হওয়ার সময় পায় |
| 4 | `core/engine.py` (Fast/curl) | 200 status-এ আসা CF error page-ও এখন failed হিসেবে গোনা হয় |
| 5 | দুই ইঞ্জিন | পরপর **১০টি** ভিজিট error page হলে ক্যাম্পেইন নিজেই থেমে যায় (প্রক্সি নষ্ট না করার জন্য) এবং কারণ লগ করে |
| 6 | `tests/test_error_page_not_counted.py` | ৭টি নতুন রিগ্রেশন টেস্ট, আপনার আসল ৫২৬ পেজের HTML দিয়ে |

লগে এখন যা দেখবেন:
```
TARGET IS NOT SERVING A PAGE: visits are landing on a Cloudflare error page
(Cloudflare 526: invalid origin SSL certificate). Real visitors see the same
error, so the site's own analytics never fires and nothing can be counted.
Fix the origin server / SSL certificate in Cloudflare first.
Stopping the campaign: the last 10 visits all landed on a Cloudflare error page
(Cloudflare 526: invalid origin SSL certificate). Sending more would only waste proxies.
```

---

## ৪. আপনার জন্য আসল কাজ (সাইটের দিকে)

1. **Cloudflare dashboard → SSL/TLS** দেখুন। অরিজিন সার্ভারের সার্টিফিকেট expired / self-signed / ডোমেইন কভার করছে না — এটাই ৫২৬ এর কারণ।
2. সার্ভারে **Cloudflare Origin CA certificate** বসান (Cloudflare ড্যাশবোর্ড থেকে ফ্রি ইস্যু হয়), অথবা হোস্টিং থেকে সার্টিফিকেট রিনিউ করান। SSL/TLS mode **Full (strict)** রাখুন।
3. ঠিক হওয়ার পর যাচাই: ব্রাউজারে লিংকটা খুলুন — error page-এর বদলে আসল পেজ এলেই বুঝবেন ঠিক হয়েছে।
4. তারপর টুল চালান। সাইট কাউন্ট করা শুরু করলে **Delivered** বাড়বে (এখন সত্যিকারের পেজ লোড হলেই গোনে), আর error page পেলে ক্যাম্পেইন থেমে সাফ বলে দেবে।

---

## ৫. ভেরিফিকেশন

| যাচাই | ফলাফল |
|---|---|
| নতুন রিগ্রেশন টেস্ট (৫২৬ পেজ শনাক্ত, আসল পেজ শনাক্ত হয় না, থামার সীমা, একবারই লগ) | ✅ ৭টি পাস |
| পুরো টেস্ট স্যুট | ✅ **৩১৭ পাস** |
| কম্পাইল | ✅ সব .py ক্লিন |

**⚠️ মনে রাখবেন:** আপনার চলমান অ্যাপটা এখনো আগের কোড চালাচ্ছে — ফিক্স কাজে লাগাতে **ক্যাম্পেইন থামিয়ে অ্যাপটা বন্ধ করে আবার চালু করুন**।
