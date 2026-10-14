# 🚀 DarkMatter Bot v3.8.0 - New Features & Cloud Capabilities

This release introduces comprehensive **Cloud / Railway.app support**, a **Cyberpunk Web Status Dashboard**, **Telegram Bot Remote Control**, **Scenario Multi-Step Traffic Engine**, and **Remote Proxy Source Auto-Sync**.

---

## 🌟 1. Railway.app Cloud Support
- **Dynamic Port Detection**: Automatically listens to Railway's `$PORT` environment variable and binds to `0.0.0.0`.
- **Health Check Endpoints**: Added `/health` and `/healthz` endpoints for zero-downtime deployment health checks.
- **Fail-safe Key Handling**: Auto-generates secure 32-char session tokens if `DM_SECRET_KEY` is not provided, preventing startup crashes.
- **One-Click Manifest**: Bundles `Dockerfile`, `railway.json`, `Procfile`, and `docker-compose.yml`.

---

## 🖥️ 2. Real-Time Web Status Dashboard & REST API
- Built-in responsive dark-themed dashboard served directly by `relay.py` at `http://<domain>/`.
- Displays real-time connected slave worker nodes, system CPU/RAM usage, uptime, and quick connection snippets.
- REST API Endpoints:
  - `GET /health` - Health check status
  - `GET /api/status` - Live cluster telemetry JSON
  - `GET /api/agents` - Connected slave node information

---

## 🤖 3. Telegram Bot Remote Controller (`telegram_bot.py`)
- Control and monitor campaigns directly from your phone or desktop via Telegram.
- **Commands**:
  - `/status` - Cluster health, CPU/RAM, and active nodes
  - `/slaves` - List connected slave nodes
  - `/traffic <url> <views> [threads]` - Launch traffic campaign
  - `/stress <url> [type] [threads]` - Launch stress testing campaign
  - `/scan <target> [ports]` - Run network port / SSH scan
  - `/stop` - Emergency stop for all running tasks
  - `/ping` - Health ping
- Sends real-time alerts when campaigns finish or slaves connect.

---

## 🧭 4. Scenario Mode - Multi-Step User Navigation (`core/scenario_engine.py`)
- Emulates realistic human browsing flows:
  1. Landing page visit
  2. Human dwell time delay (2–6s)
  3. Automatic internal link extraction and multi-page journey traversal
  4. Proper Referer header chains (`Referer: <landing_url>`)
  5. Configurable bounce rate simulation
- Drastically increases analytics realism for Google Analytics 4, Cloudflare, and Datadome.

---

## 🔄 5. Auto-Proxy Sources Sync (`core/source_updater.py`)
- Automatically scrapes and synchronizes fresh, tested proxy lists from top public repositories (TheSpeedX, monosans, clarketm, etc.).
- Auto-deduplicates and merges endpoints into `resources/sources.txt`.
