# রাউন্ড ১০ — "Exit IP: unknown" ফিক্স + লগে দেশ দেখানো

তারিখ: ২০২৬-১০-১২

---

## ১. "unknown" কেন আসত

আপনার লগে কারণটা ছিল:

```
[00:29:42] Starting Tor / connecting to the control port ...
[00:30:47] Tor running (0.4.9.13)        <- ৬৫ সেকেন্ড পর
[00:30:47] Tor ready. Current exit IP: unknown
```

Tor **কন্ট্রোল পোর্ট আগে খোলে**, কিন্তু ট্রাফিক পাঠানোর SOCKS circuit তৈরি হতে আরও সময় লাগে। তখনই একবার exit IP আনার চেষ্টা হয় → SOCKS তৈরি না থাকায় কিছু ফেরত আসে না → `unknown`।

**যা ঠিক করা হলো:**
1. IP আনার আগে **SOCKS পোর্ট খোলা কি না** যাচাই (`_port_listening`)
2. **৩ বার রিট্রাই**, প্রতি চেষ্টায় ২৫s সময়সীমা, মাঝে ৩s বিরতি
3. নতুন `wait_for_exit_ip(timeout=90)` — Tor চালু হওয়ার পর circuit তৈরি পর্যন্ত অপেক্ষা করে (এখন `Tor ready` লেখার আগেই IP পাওয়া যায়)
4. ব্যর্থ হলে **কারণ লেখা হয়**, শুধু "unknown" নয় — যেমন *"could not read the exit IP through Tor (…): Tor may still be starting"*

## ২. কোন দেশ দিয়ে কাজ করছে — এখন লগে দেখা যায়

দেশ বের করা হয় আপনার অ্যাপের নিজের **GeoLite2 ডেটাবেস** দিয়ে (`core/tor_manager.exit_ip_country`) — ইন্টারনেট বা API লাগে না। প্রথমে দেশের নাম, না থাকলে কোড, সাথে শহর (যদি থাকে)।

**লগে যেখানে যেখানে দেখাবে:**

| সময় | লগ লাইন |
|---|---|
| Tor রেডি | `Tor running (v0.4.9.13), exit IP 204.8.96.153 (United States)` |
| ক্যাম্পেইন শুরু (Tor মোড) | `IP source: Tor (socks5://127.0.0.1:9050) - traffic leaves through 204.8.96.153 (United States)` |
| প্রতি IP বদলে | `IP switched - now going out through 45.66.35.29 (Netherlands)` |
| TEST / ROTATE চাপলে | `Tor exit IP: 171.25.193.37 (Sweden)` → `IP changed: 171.25.193.37 -> 192.42.116.117 (Netherlands)` |
| প্রক্সি লিস্ট মোড | `[via 7/20] 198.46.161.42:5092 - US Los Angeles` (আগে থেকেই ছিল) |

## ৩. লাইভ প্রমাণ (আপনার নিজের Tor-এ)

```
attached        : True True
describe()      : Tor running (v0.4.9.13), exit IP 171.25.193.37 (SE)
--- rotate ---
exit label after: 192.42.116.117 (NL)

দেশের নাম যাচাই:
94.156.189.0 -> Cyprus
8.8.8.8      -> United States
live label   : 204.8.96.153 (United States)
```

`unknown` আর আসছে না, দেশ প্রতিবার দেখাচ্ছে।

## ৪. ভেরিফিকেশন
- **৩৫৮ টেস্ট পাস** (Tor-এর টেস্টগুলো এখন "Tor নেই / control পোর্ট নেই / auth refuse" — তিন অবস্থাই পরীক্ষা করে)
- **লাইভ GUI চেক: 0 failures, ALL PASS**

## ৫. করুন
**অ্যাপ রিস্টার্ট করুন** → `IP source = Tor (free)` → **START TOR** → **TEST / ROTATE** → START CAMPAIGN।
লগে এখন প্রতিবার IP আর দেশ দুটোই দেখা যাবে।
