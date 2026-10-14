"""
DarkMatter Traffic Bot - Web Dashboard & API Endpoints

Provides a modern cyberpunk web status dashboard and REST API for Relay/Master servers,
especially when deployed on cloud platforms like Railway.
"""

import time
import os
import sys

try:
    import psutil
except (ImportError, ModuleNotFoundError):
    psutil = None

DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DarkMatter Bot - Cloud Relay Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Orbitron:wght@700;900&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0a0c12;
            --bg-card: #131722;
            --bg-card-hover: #181d2b;
            --border-color: #232a3b;
            --border-glow: #3b82f640;
            --accent-blue: #3b82f6;
            --accent-cyan: #22d3ee;
            --accent-purple: #a855f7;
            --status-green: #10b981;
            --status-green-glow: #10b98155;
            --status-red: #ef4444;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Rajdhani', -apple-system, sans-serif;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px;
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(59, 130, 246, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(34, 211, 238, 0.06) 0%, transparent 40%),
                linear-gradient(rgba(255, 255, 255, 0.01) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.01) 1px, transparent 1px);
            background-size: 100% 100%, 100% 100%, 32px 32px, 32px 32px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        /* Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 28px;
            flex-wrap: wrap;
            gap: 16px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .logo-box {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, #1e3a8a, #0284c7);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
            border: 1px solid var(--accent-cyan);
        }

        .logo-box svg {
            width: 28px;
            height: 28px;
            fill: #ffffff;
        }

        .brand-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 24px;
            font-weight: 900;
            letter-spacing: 1.5px;
            background: linear-gradient(90deg, #60a5fa, #22d3ee);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand-subtitle {
            font-size: 13px;
            color: var(--text-muted);
            letter-spacing: 0.5px;
        }

        .status-pill {
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 8px 16px;
            border-radius: 999px;
            font-weight: 600;
            font-size: 14px;
            color: var(--status-green);
            box-shadow: 0 0 15px var(--status-green-glow);
        }

        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: var(--status-green);
            box-shadow: 0 0 8px var(--status-green);
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.2); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.8; }
        }

        /* Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 28px;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 20px;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
            opacity: 0;
            transition: opacity 0.2s ease;
        }

        .stat-card:hover {
            border-color: var(--border-glow);
            transform: translateY(-2px);
            background: var(--bg-card-hover);
        }

        .stat-card:hover::before {
            opacity: 1;
        }

        .stat-label {
            font-size: 13px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 32px;
            font-weight: 700;
            color: var(--text-main);
            display: flex;
            align-items: baseline;
            gap: 6px;
        }

        .stat-unit {
            font-size: 14px;
            color: var(--text-dim);
            font-weight: 400;
        }

        /* Content Section */
        .section-box {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 24px;
            margin-bottom: 28px;
        }

        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            padding-bottom: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .section-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 16px;
            letter-spacing: 1px;
            color: var(--accent-cyan);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* Table */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }

        th {
            text-align: left;
            padding: 12px 14px;
            color: var(--text-dim);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            border-bottom: 1px solid var(--border-color);
        }

        td {
            padding: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            color: var(--text-main);
        }

        tr:hover td {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .mono {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
        }

        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .badge-online {
            background: rgba(16, 185, 129, 0.15);
            color: var(--status-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-controller {
            background: rgba(59, 130, 246, 0.15);
            color: var(--accent-blue);
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        /* Quick Info Cards */
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
        }

        .code-snippet {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: #58a6ff;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-all;
            margin-top: 10px;
            position: relative;
        }

        /* Footer */
        footer {
            text-align: center;
            color: var(--text-dim);
            font-size: 13px;
            padding: 20px 0;
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 10px;
        }

        footer a {
            color: var(--accent-cyan);
            text-decoration: none;
        }

        footer a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="brand">
                <div class="logo-box">
                    <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
                </div>
                <div>
                    <div class="brand-title">DARKMATTER BOT</div>
                    <div class="brand-subtitle">Cloud Relay &amp; Cluster Controller v3.8.0</div>
                </div>
            </div>
            <div class="status-pill">
                <div class="status-dot"></div>
                <span>RELAY ACTIVE (RAILWAY READY)</span>
            </div>
        </header>

        <!-- Stats Grid -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Connected Slaves</div>
                <div class="stat-value">__AGENT_COUNT__ <span class="stat-unit">nodes</span></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Controllers Active</div>
                <div class="stat-value">__CONTROLLER_COUNT__ <span class="stat-unit">clients</span></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Uptime</div>
                <div class="stat-value">__UPTIME__</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">System Resources</div>
                <div class="stat-value">__CPU_USAGE__% <span class="stat-unit">CPU / __RAM_MB__ MB</span></div>
            </div>
        </div>

        <!-- Connected Nodes Section -->
        <div class="section-box">
            <div class="section-header">
                <div class="section-title">
                    <span>⚡ Connected Cluster Nodes (Slaves)</span>
                </div>
                <span class="stat-unit">Auto-refreshing</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Node Name</th>
                        <th>IP Address</th>
                        <th>Connection Time</th>
                        <th>Last Heartbeat</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    __AGENT_ROWS__
                </tbody>
            </table>
        </div>

        <!-- Connection Details & Setup Guides -->
        <div class="info-grid">
            <div class="section-box" style="margin-bottom:0;">
                <div class="section-title">🔗 Connect Desktop App to this Relay</div>
                <p style="color:var(--text-muted); font-size:14px; margin-top:8px;">
                    In DarkMatterBot Desktop UI &gt; <strong>Master Control</strong> &gt; Select <strong>Relay Mode</strong>:
                </p>
                <div class="code-snippet">Host: __HOST_DOMAIN__
Port: __HOST_PORT__ (or 443 / 80 on Railway)
Secret: [Your DM_SECRET_KEY]</div>
            </div>

            <div class="section-box" style="margin-bottom:0;">
                <div class="section-title">🚀 Connect Linux / Cloud Slave to this Relay</div>
                <p style="color:var(--text-muted); font-size:14px; margin-top:8px;">
                    Run on any VPS, Docker, or Raspberry Pi:
                </p>
                <div class="code-snippet">python slave.py --mode relay --master __HOST_DOMAIN__:__HOST_PORT__ --secret "YOUR_SECRET_KEY" --name slave-01</div>
            </div>
        </div>

        <div style="height: 28px;"></div>

        <!-- API Endpoints Info -->
        <div class="section-box">
            <div class="section-title">📡 REST &amp; WebSocket Endpoints</div>
            <div style="margin-top:12px; display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:12px;">
                <div style="background:#0d1117; padding:12px; border-radius:8px; border:1px solid #30363d;">
                    <div style="color:var(--status-green); font-weight:700; font-size:13px;">GET /health</div>
                    <div style="color:var(--text-muted); font-size:12px;">Railway health check endpoint</div>
                </div>
                <div style="background:#0d1117; padding:12px; border-radius:8px; border:1px solid #30363d;">
                    <div style="color:var(--accent-cyan); font-weight:700; font-size:13px;">GET /api/status</div>
                    <div style="color:var(--text-muted); font-size:12px;">Live JSON cluster stats</div>
                </div>
                <div style="background:#0d1117; padding:12px; border-radius:8px; border:1px solid #30363d;">
                    <div style="color:var(--accent-purple); font-weight:700; font-size:13px;">GET /api/agents</div>
                    <div style="color:var(--text-muted); font-size:12px;">Connected slave nodes list</div>
                </div>
                <div style="background:#0d1117; padding:12px; border-radius:8px; border:1px solid #30363d;">
                    <div style="color:var(--accent-blue); font-weight:700; font-size:13px;">WS /ws</div>
                    <div style="color:var(--text-muted); font-size:12px;">HMAC authenticated WebSocket</div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <footer>
            <div>DarkMatter Traffic Bot v3.8.0 • Cloud Ready</div>
            <div>Built for QA, Stress Testing &amp; Security Research</div>
        </footer>
    </div>
</body>
</html>
"""


def format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime."""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)

    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def render_dashboard(
    agents: dict,
    controllers: dict,
    start_time: float,
    host: str = "0.0.0.0",
    port: int = 8765,
    request_host: str = "",
) -> str:
    """Render the HTML dashboard with live server values."""
    uptime_str = format_uptime(time.time() - start_time)

    # Get system metrics
    try:
        cpu_usage = int(psutil.cpu_percent(interval=None))
        mem = psutil.virtual_memory()
        ram_mb = int(mem.used / (1024 * 1024))
    except Exception:
        cpu_usage = 0
        ram_mb = 0

    # Build agent table rows
    if not agents:
        agent_rows = """
        <tr>
            <td colspan="5" style="text-align:center; color:var(--text-dim); padding:24px;">
                No slave nodes connected yet. Deploy slave instances to scale traffic generation!
            </td>
        </tr>
        """
    else:
        rows = []
        now = time.time()
        for client in agents.values():
            conn_time = format_uptime(now - client.connected_at) + " ago"
            hb_time = f"{int(now - client.last_heartbeat)}s ago"
            rows.append(f"""
            <tr>
                <td><strong style="color:var(--text-main);">{client.name}</strong> <span style="color:var(--text-dim); font-size:12px;">({client.client_id[:8]})</span></td>
                <td class="mono">{client.ip_address}</td>
                <td>{conn_time}</td>
                <td>{hb_time}</td>
                <td><span class="badge badge-online">CONNECTED</span></td>
            </tr>
            """)
        agent_rows = "\n".join(rows)

    display_domain = request_host or (f"{host}:{port}" if host != "0.0.0.0" else "your-app.railway.app")
    display_port = str(port)

    html = DASHBOARD_HTML_TEMPLATE
    html = html.replace("__AGENT_COUNT__", str(len(agents)))
    html = html.replace("__CONTROLLER_COUNT__", str(len(controllers)))
    html = html.replace("__UPTIME__", uptime_str)
    html = html.replace("__CPU_USAGE__", str(cpu_usage))
    html = html.replace("__RAM_MB__", str(ram_mb))
    html = html.replace("__AGENT_ROWS__", agent_rows)
    html = html.replace("__HOST_DOMAIN__", display_domain)
    html = html.replace("__HOST_PORT__", display_port)
    return html


def get_health_status(agents: dict, controllers: dict, start_time: float) -> dict:
    """Return JSON health check payload."""
    return {
        "status": "ok",
        "service": "darkmatter-relay",
        "version": "3.8.0",
        "timestamp": time.time(),
        "uptime_seconds": round(time.time() - start_time, 2),
        "agents_connected": len(agents),
        "controllers_connected": len(controllers),
    }


def get_cluster_stats(agents: dict, controllers: dict, start_time: float) -> dict:
    """Return full cluster telemetry stats in JSON format."""
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        mem_used_mb = round(mem.used / (1024 * 1024), 2)
        mem_percent = mem.percent
    except Exception:
        cpu_percent = 0.0
        mem_used_mb = 0.0
        mem_percent = 0.0

    return {
        "status": "online",
        "version": "3.8.0",
        "uptime_seconds": round(time.time() - start_time, 2),
        "system": {
            "cpu_percent": cpu_percent,
            "memory_used_mb": mem_used_mb,
            "memory_percent": mem_percent,
            "platform": sys.platform,
        },
        "cluster": {
            "agents_count": len(agents),
            "controllers_count": len(controllers),
            "agents": [
                {
                    "id": a.client_id,
                    "name": a.name,
                    "ip": a.ip_address,
                    "connected_at": a.connected_at,
                    "last_heartbeat": a.last_heartbeat,
                    "extra_info": getattr(a, "extra_info", {}),
                }
                for a in agents.values()
            ],
        },
    }
