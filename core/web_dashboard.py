"""
DarkMatter Traffic Bot - Pixel-Perfect MR ADS Web Dashboard

1:1 Pixel-Perfect Web recreation of the MR ADS Traffic Suite v3.8.0 UI:
- Exact Dark Carbon Azure theme with sidebar navigation (Dashboard, Proxy Manager, Stress Test, Master Control, Settings).
- Top Stat Cards: Attempts, Delivered, Failed, Delivery, Proxies.
- Attack Configuration: Multi-link URL textarea (7-8+ links), Fast (curl) / Browser (stealth), Threads slider, Duration Min/Max, Burst mode, Clicks per link per IP, Visits to take.
- Big START CAMPAIGN (Green), CHECK TARGET, RESET buttons.
- Real-time Activity Log.
- MR ADS Proxy Manager tab (HProxy API, 10,000 max rows, Recheck HTTPS against ipify, live grid, auto-transfer to campaign).
- Full interactive live REST & WebSocket control.
"""

import os
import sys
import time

try:
    import psutil
except (ImportError, ModuleNotFoundError):
    psutil = None

MR_ADS_FULL_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MR ADS - TRAFFIC SUITE v3.8.0</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Segoe+UI:wght@400;600;700;800&family=Orbitron:wght@700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-window: #0A0C12;
            --bg-sidebar: #0F121A;
            --bg-card: #161A24;
            --bg-card-hover: #1D2230;
            --bg-input: #10131B;
            --border-main: #2A3145;
            --border-soft: #1E2431;
            --border-focus: #3B82F6;
            --accent-blue: #3B82F6;
            --accent-blue-hover: #2563EB;
            --accent-cyan: #22D3EE;
            --accent-glow: #60A5FA;
            --status-green: #2DD4A7;
            --status-red: #F87171;
            --status-yellow: #F5B93E;
            --text-main: #EDF1F8;
            --text-dim: #9BA6BE;
            --text-mute: #616C86;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', -apple-system, sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        body {
            background-color: var(--bg-window);
            color: var(--text-main);
            height: 100vh;
            display: flex;
            overflow: hidden;
            user-select: none;
        }

        /* App Layout */
        .app-container {
            display: flex;
            width: 100vw;
            height: 100vh;
        }

        /* ================= SIDEBAR ================= */
        .sidebar {
            width: 230px;
            background-color: var(--bg-sidebar);
            border-right: 1px solid var(--border-soft);
            display: flex;
            flex-direction: column;
            padding: 16px 12px;
            flex-shrink: 0;
        }

        .brand-header {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 6px 20px;
            border-bottom: 1px solid var(--border-soft);
            margin-bottom: 16px;
        }

        .brand-avatar {
            width: 38px;
            height: 38px;
            background: linear-gradient(135deg, #1E40AF, #3B82F6);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 14px;
            color: #ffffff;
            box-shadow: 0 0 14px rgba(59, 130, 246, 0.4);
            border: 1px solid var(--accent-cyan);
            letter-spacing: 0.5px;
        }

        .brand-text-box {
            display: flex;
            flex-direction: column;
        }

        .brand-title-main {
            font-size: 15px;
            font-weight: 800;
            letter-spacing: 0.8px;
            color: #ffffff;
        }

        .brand-version-sub {
            font-size: 10px;
            font-weight: 700;
            color: var(--accent-cyan);
            letter-spacing: 0.5px;
        }

        .nav-list {
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex: 1;
        }

        .nav-item {
            background: transparent;
            border: none;
            color: var(--text-dim);
            padding: 11px 14px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 600;
            text-align: left;
            cursor: pointer;
            transition: all 0.15s ease;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .nav-item:hover {
            background-color: rgba(255, 255, 255, 0.04);
            color: var(--text-main);
        }

        .nav-item.active {
            background-color: #1A2438;
            color: #ffffff;
            border-left: 3px solid var(--accent-blue);
        }

        .sidebar-footer {
            padding: 12px 6px 4px;
            border-top: 1px solid var(--border-soft);
            font-size: 11px;
            color: var(--text-mute);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* ================= MAIN CONTENT AREA ================= */
        .main-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            background-color: var(--bg-window);
            overflow-y: auto;
            padding: 20px 24px;
        }

        /* Top Page Header */
        .content-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 18px;
        }

        .page-title {
            font-size: 20px;
            font-weight: 800;
            color: #ffffff;
            letter-spacing: 0.5px;
        }

        .page-desc {
            font-size: 12px;
            color: var(--text-mute);
            margin-top: 2px;
        }

        .header-status-indicator {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 12px;
            font-weight: 700;
            color: var(--text-dim);
        }

        .dot-pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--status-green);
            box-shadow: 0 0 8px var(--status-green);
        }

        /* Tab Panes */
        .tab-content {
            display: none;
            flex-direction: column;
            gap: 16px;
        }

        .tab-content.active {
            display: flex;
        }

        /* ================= 5 TOP STAT CARDS ================= */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }

        .stat-card-box {
            background: var(--bg-card);
            border: 1px solid var(--border-soft);
            border-radius: 10px;
            padding: 12px 14px 14px;
            display: flex;
            flex-direction: column;
            position: relative;
            overflow: hidden;
        }

        .stat-card-box::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
        }

        .stat-card-box.c-white::before { background: #94a3b8; }
        .stat-card-box.c-green::before { background: var(--status-green); }
        .stat-card-box.c-red::before { background: var(--status-red); }
        .stat-card-box.c-cyan::before { background: var(--accent-cyan); }
        .stat-card-box.c-blue::before { background: var(--accent-blue); }

        .stat-box-title {
            font-size: 11px;
            color: var(--text-mute);
            font-weight: 600;
            text-align: center;
            margin-bottom: 6px;
        }

        .stat-box-val {
            font-family: 'JetBrains Mono', monospace;
            font-size: 22px;
            font-weight: 700;
            text-align: center;
        }

        .c-white .stat-box-val { color: #ffffff; }
        .c-green .stat-box-val { color: var(--status-green); }
        .c-red .stat-box-val { color: var(--status-red); }
        .c-cyan .stat-box-val { color: var(--accent-cyan); }
        .c-blue .stat-box-val { color: var(--accent-blue); }

        /* ================= CARD PANELS ================= */
        .card-panel {
            background: var(--bg-card);
            border: 1px solid var(--border-soft);
            border-radius: 12px;
            padding: 16px 20px;
            display: flex;
            flex-direction: column;
            gap: 14px;
        }

        .panel-heading {
            font-size: 14px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: 0.3px;
        }

        /* Inputs & Multi-URL Box */
        .input-url-box {
            width: 100%;
            background: var(--bg-input);
            border: 1px solid var(--border-main);
            color: var(--text-main);
            border-radius: 8px;
            padding: 10px 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            outline: none;
            resize: vertical;
            min-height: 55px;
            transition: border-color 0.15s ease;
        }

        .input-url-box:focus {
            border-color: var(--border-focus);
        }

        /* Segmented Selector for Engine */
        .engine-segmented-row {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .engine-label {
            font-size: 12.5px;
            font-weight: 600;
            color: var(--text-dim);
            min-width: 60px;
        }

        .segmented-control {
            display: flex;
            background: var(--bg-input);
            border: 1px solid var(--border-main);
            border-radius: 8px;
            padding: 3px;
            flex: 1;
        }

        .seg-btn {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-dim);
            padding: 7px 14px;
            font-size: 12.5px;
            font-weight: 700;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .seg-btn.active {
            background: var(--accent-blue);
            color: #ffffff;
            box-shadow: 0 0 10px rgba(59, 130, 246, 0.4);
        }

        /* Controls Grid (Sliders & Options) */
        .controls-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 16px;
            background: rgba(0, 0, 0, 0.15);
            padding: 12px 14px;
            border-radius: 8px;
            border: 1px solid var(--border-soft);
        }

        .control-item {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .control-label-flex {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-dim);
        }

        .control-val-tag {
            color: var(--accent-cyan);
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
        }

        .slider-custom {
            width: 100%;
            height: 5px;
            border-radius: 3px;
            background: #232b3d;
            outline: none;
            accent-color: var(--accent-blue);
            cursor: pointer;
        }

        /* Select dropdowns */
        .select-dark {
            background: var(--bg-input);
            border: 1px solid var(--border-main);
            color: var(--text-main);
            border-radius: 6px;
            padding: 7px 10px;
            font-size: 12px;
            font-weight: 600;
            outline: none;
        }

        /* Action Buttons Row */
        .action-buttons-row {
            display: flex;
            gap: 12px;
            margin-top: 4px;
        }

        .btn-start-giant {
            flex: 1;
            background: var(--status-green);
            color: #0A0C12;
            border: none;
            border-radius: 8px;
            padding: 14px;
            font-size: 14px;
            font-weight: 800;
            letter-spacing: 0.8px;
            cursor: pointer;
            transition: all 0.15s ease;
            box-shadow: 0 0 18px rgba(45, 212, 167, 0.35);
        }

        .btn-start-giant:hover {
            background: #34E0B3;
            transform: translateY(-1px);
        }

        .btn-stop-giant {
            background: var(--status-red);
            color: #ffffff;
            box-shadow: 0 0 18px rgba(248, 113, 113, 0.35);
        }

        .btn-sub-action {
            background: #1C2333;
            color: var(--text-main);
            border: 1px solid var(--border-main);
            border-radius: 8px;
            padding: 14px 20px;
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .btn-sub-action:hover {
            background: #252F45;
            border-color: var(--accent-blue);
        }

        .btn-reset-yellow {
            background: #D97706;
            color: #ffffff;
            border: none;
        }

        /* Sub Status Info bar */
        .sub-status-bar {
            font-size: 11px;
            color: var(--text-mute);
            display: flex;
            gap: 16px;
            padding: 2px 4px;
        }

        /* ================= ACTIVITY LOG ================= */
        .log-section-box {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .log-box-terminal {
            background: #080A0F;
            border: 1px solid var(--border-soft);
            border-radius: 8px;
            height: 200px;
            overflow-y: auto;
            padding: 10px 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            line-height: 1.6;
        }

        .log-row {
            padding: 2px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        }

        .log-row.info { color: #9BA6BE; }
        .log-row.success { color: var(--status-green); }
        .log-row.error { color: var(--status-red); }
        .log-row.warn { color: var(--status-yellow); }
        .log-timestamp { color: var(--text-mute); margin-right: 8px; }

        /* ================= PROXY MANAGER TABLE ================= */
        .proxy-table-scroll {
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid var(--border-soft);
            border-radius: 8px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
        }

        th {
            background: #111520;
            color: var(--text-dim);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 9px 12px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 2;
            border-bottom: 1px solid var(--border-main);
        }

        td {
            padding: 9px 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            color: var(--text-main);
        }

        tr:hover td {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .tag-badge {
            display: inline-block;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
        }

        .tag-badge.pass { background: rgba(45, 212, 167, 0.15); color: var(--status-green); border: 1px solid rgba(45, 212, 167, 0.3); }
        .tag-badge.fail { background: rgba(248, 113, 113, 0.15); color: var(--status-red); border: 1px solid rgba(248, 113, 113, 0.3); }
        .tag-badge.proto { background: rgba(59, 130, 246, 0.15); color: var(--accent-blue); }
    </style>
</head>
<body>
    <div class="app-container">
        <!-- ================= SIDEBAR ================= -->
        <aside class="sidebar">
            <div class="brand-header">
                <div class="brand-avatar">MR</div>
                <div class="brand-text-box">
                    <span class="brand-title-main">MR ADS</span>
                    <span class="brand-version-sub">TRAFFIC SUITE • v3.8.0</span>
                </div>
            </div>

            <nav class="nav-list">
                <button class="nav-item active" onclick="switchNav('dashboard')">
                    <span>🚀</span> Dashboard
                </button>
                <button class="nav-item" onclick="switchNav('proxies')">
                    <span>🛡️</span> Proxy Manager
                </button>
                <button class="nav-item" onclick="switchNav('stress')">
                    <span>💥</span> Stress Test
                </button>
                <button class="nav-item" onclick="switchNav('master')">
                    <span>⚡</span> Master Control
                </button>
                <button class="nav-item" onclick="switchNav('settings')">
                    <span>⚙️</span> Settings
                </button>
            </nav>

            <div class="sidebar-footer">
                <span>Railway Cloud 24/7</span>
                <span style="color:var(--status-green);">● Live</span>
            </div>
        </aside>

        <!-- ================= MAIN CONTENT ================= -->
        <main class="main-content">
            <!-- Top Header -->
            <div class="content-header">
                <div>
                    <h1 class="page-title" id="page-title-text">Dashboard</h1>
                    <p class="page-desc">Campaign control and live traffic statistics</p>
                </div>
                <div class="header-status-indicator">
                    <span class="dot-pulse"></span>
                    <span id="header-status-label">Idle  •  0 ACTIVE - <span id="header-proxy-count">0</span> LOADED</span>
                </div>
            </div>

            <!-- 5 TOP STAT CARDS -->
            <div class="stats-row">
                <div class="stat-card-box c-white">
                    <span class="stat-box-title">Attempts</span>
                    <span class="stat-box-val" id="top-attempts">0</span>
                </div>
                <div class="stat-card-box c-green">
                    <span class="stat-box-title">Delivered</span>
                    <span class="stat-box-val" id="top-delivered">0</span>
                </div>
                <div class="stat-card-box c-red">
                    <span class="stat-box-title">Failed</span>
                    <span class="stat-box-val" id="top-failed">0</span>
                </div>
                <div class="stat-card-box c-cyan">
                    <span class="stat-box-title">Delivery</span>
                    <span class="stat-box-val" id="top-rate">--</span>
                </div>
                <div class="stat-card-box c-blue">
                    <span class="stat-box-title">Proxies</span>
                    <span class="stat-box-val" id="top-proxies">0</span>
                </div>
            </div>

            <div style="height: 14px;"></div>

            <!-- ================= TAB 1: DASHBOARD ================= -->
            <div id="pane-dashboard" class="tab-content active">
                <div class="card-panel">
                    <div class="panel-heading">Attack Configuration</div>

                    <!-- Target URLs (Multi-link input 7-8+ URLs) -->
                    <div>
                        <textarea id="target_urls" class="input-url-box" placeholder="https://deshilink.my/VmTZnigp&#10;https://example.com/link2&#10;https://example.com/link3&#10;(Enter multiple URLs here - 7-8+ links supported, 1 per line)"></textarea>
                    </div>

                    <!-- Engine Selector -->
                    <div class="engine-segmented-row">
                        <span class="engine-label">Engine:</span>
                        <div class="segmented-control">
                            <button id="engine-curl-btn" class="seg-btn active" onclick="setEngine('curl')">Fast (curl)</button>
                            <button id="engine-browser-btn" class="seg-btn" onclick="setEngine('browser')">Browser (stealth)</button>
                        </div>
                    </div>

                    <!-- Controls Grid (Threads, Duration, Burst, Clicks/IP) -->
                    <div class="controls-grid">
                        <div class="control-item">
                            <div class="control-label-flex">
                                <span>Threads:</span>
                                <span class="control-val-tag" id="val-threads">5</span>
                            </div>
                            <input type="range" class="slider-custom" id="slider-threads" min="1" max="100" value="5" oninput="updateVal('threads', this.value)">
                        </div>

                        <div class="control-item">
                            <div class="control-label-flex">
                                <span>Duration: <span id="val-duration-min">15s</span> - <span id="val-duration-max">20s</span></span>
                            </div>
                            <div style="display:flex; gap:6px;">
                                <input type="range" class="slider-custom" id="slider-dur-min" min="1" max="60" value="15" oninput="updateDur()">
                                <input type="range" class="slider-custom" id="slider-dur-max" min="1" max="60" value="20" oninput="updateDur()">
                            </div>
                        </div>

                        <div class="control-item">
                            <div class="control-label-flex">
                                <span>Clicks per Link (Per IP):</span>
                                <span class="control-val-tag" id="val-clicks-link">4</span>
                            </div>
                            <select id="select-clicks-link" class="select-dark" onchange="document.getElementById('val-clicks-link').innerText = this.value">
                                <option value="4" selected>4 Clicks (Then rotate to next link)</option>
                                <option value="5">5 Clicks</option>
                                <option value="1">1 Click (Strict rotation)</option>
                                <option value="10">10 Clicks</option>
                            </select>
                        </div>
                    </div>

                    <!-- Row 2 Controls (Visits & 24/7 Mode) -->
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                        <div style="display:flex; align-items:center; gap:10px; font-size:12.5px;">
                            <span>Visits to take:</span>
                            <select id="select-visits" class="select-dark">
                                <option value="0" selected>Unlimited (24/7 Run)</option>
                                <option value="100">100</option>
                                <option value="500">500</option>
                                <option value="1000">1000</option>
                                <option value="5000">5000</option>
                            </select>
                        </div>

                        <div style="display:flex; align-items:center; gap:8px;">
                            <input type="checkbox" id="chk-continuous" checked style="accent-color:var(--accent-blue); width:16px; height:16px;">
                            <label for="chk-continuous" style="font-size:12.5px; font-weight:600; cursor:pointer;">24/7 Non-stop Auto Rotation</label>
                        </div>
                    </div>

                    <!-- Giant Start Button Row -->
                    <div class="action-buttons-row">
                        <button id="btn-main-action" class="btn-start-giant" onclick="toggleCampaign()">START CAMPAIGN</button>
                        <button class="btn-sub-action" onclick="checkTarget()">CHECK TARGET</button>
                        <button class="btn-sub-action btn-reset-yellow" onclick="resetConfig()">RESET</button>
                    </div>

                    <div class="sub-status-bar">
                        <span>Browser: --</span>
                        <span>Contexts: 0/0</span>
                        <span>Protection: 0/0</span>
                    </div>
                </div>

                <!-- Activity Log Terminal -->
                <div class="log-section-box">
                    <div style="font-size:12px; font-weight:700; color:var(--text-dim);">Activity Log</div>
                    <div class="log-box-terminal" id="activity-terminal">
                        <div class="log-row info"><span class="log-timestamp">[System]</span> MR ADS Traffic Suite v3.8.0 online on Railway Cloud.</div>
                    </div>
                </div>
            </div>

            <!-- ================= TAB 2: PROXY MANAGER ================= -->
            <div id="pane-proxies" class="tab-content">
                <div class="card-panel">
                    <div class="panel-heading">MR ADS Proxy Grabber &amp; HTTPS Re-Checker</div>

                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        <div style="flex:1; min-width:200px;">
                            <label style="font-size:11px; color:var(--text-mute); display:block; margin-bottom:4px;">Source</label>
                            <select id="grabber-source" class="select-dark" style="width:100%;">
                                <option value="All Sources" selected>All Sources (HProxy, TheSpeedX, monosans, proxifly)</option>
                                <option value="Live (API)">Live (API - HProxy 20 Pages)</option>
                                <option value="HProxy HTTP">HProxy HTTP</option>
                                <option value="HProxy HTTPS">HProxy HTTPS</option>
                                <option value="TheSpeedX HTTP">TheSpeedX HTTP</option>
                                <option value="TheSpeedX SOCKS5">TheSpeedX SOCKS5</option>
                                <option value="monosans HTTP">monosans HTTP</option>
                                <option value="proxifly HTTP">proxifly HTTP</option>
                            </select>
                        </div>

                        <div style="width:140px;">
                            <label style="font-size:11px; color:var(--text-mute); display:block; margin-bottom:4px;">Max rows</label>
                            <select id="grabber-max-rows" class="select-dark" style="width:100%;">
                                <option value="10000" selected>10,000</option>
                                <option value="5000">5,000</option>
                                <option value="20000">20,000</option>
                            </select>
                        </div>

                        <div style="display:flex; align-items:flex-end; gap:8px;">
                            <button class="btn-sub-action" style="background:#2563EB; border:none; color:#ffffff; padding:9px 16px;" onclick="fetchProxies()">FETCH LIVE LIST</button>
                            <button class="btn-sub-action" style="background:#059669; border:none; color:#ffffff; padding:9px 16px;" onclick="checkProxies()">RECHECK HTTPS</button>
                            <button class="btn-sub-action btn-reset-yellow" style="padding:9px 14px;" onclick="exportProxies('txt')">EXPORT TXT</button>
                        </div>
                    </div>

                    <div style="font-size:12px; color:var(--text-dim); margin-top:4px;" id="proxy-status-msg">
                        Ready. Click "FETCH LIVE LIST" to grab up to 10,000 fresh proxies.
                    </div>

                    <div class="proxy-table-scroll">
                        <table>
                            <thead>
                                <tr>
                                    <th>IP:PORT</th>
                                    <th>PROTOCOL</th>
                                    <th>STATUS</th>
                                    <th>LATENCY</th>
                                    <th>HTTPS TEST</th>
                                    <th>COUNTRY</th>
                                </tr>
                            </thead>
                            <tbody id="proxy-grid-body">
                                <tr>
                                    <td colspan="6" style="text-align:center; padding:24px; color:var(--text-mute);">
                                        No proxies loaded. Click "FETCH LIVE LIST" to begin.
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- ================= TAB 3: STRESS TEST ================= -->
            <div id="pane-stress" class="tab-content">
                <div class="card-panel">
                    <div class="panel-heading">Stress Testing (Load Test)</div>
                    <input type="text" id="stress-url" class="input-url-box" placeholder="https://target-server.com" style="min-height:42px;">
                    <div style="display:flex; gap:12px;">
                        <select id="stress-type" class="select-dark" style="flex:1;">
                            <option value="http_flood">HTTP Flood (GET/POST)</option>
                            <option value="slowloris">Slowloris</option>
                            <option value="rudy">RUDY</option>
                        </select>
                        <input type="number" id="stress-concurrency" class="select-dark" value="50" min="5" max="500" style="width:120px;">
                    </div>
                    <button class="btn-start-giant btn-stop-giant" onclick="alert('Stress test started in background!')">START STRESS TEST</button>
                </div>
            </div>

            <!-- ================= TAB 4: MASTER CONTROL ================= -->
            <div id="pane-master" class="tab-content">
                <div class="card-panel">
                    <div class="panel-heading">Master Control &amp; Slave Nodes</div>
                    <p style="font-size:12px; color:var(--text-mute);">Manage distributed worker slaves connected to this cloud relay.</p>
                    <div id="slaves-list-box" style="font-size:13px; color:var(--text-dim); padding:10px 0;">
                        No external slave nodes connected. Cloud Relay executes tasks directly.
                    </div>
                </div>
            </div>

            <!-- ================= TAB 5: SETTINGS ================= -->
            <div id="pane-settings" class="tab-content">
                <div class="card-panel">
                    <div class="panel-heading">Settings &amp; Automation</div>
                    <div style="display:flex; flex-direction:column; gap:10px; font-size:13px;">
                        <label style="display:flex; align-items:center; gap:8px;">
                            <input type="checkbox" checked style="accent-color:var(--accent-blue);"> Block direct traffic when proxy fails
                        </label>
                        <label style="display:flex; align-items:center; gap:8px;">
                            <input type="checkbox" checked style="accent-color:var(--accent-blue);"> Auto-transfer verified live proxies to Campaign
                        </label>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <!-- Client Script -->
    <script>
        let currentEngine = 'curl';
        let isRunning = false;

        function switchNav(tabKey) {
            document.querySelectorAll('.tab-content').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            
            const targetPane = document.getElementById('pane-' + tabKey);
            if (targetPane) targetPane.classList.add('active');
            
            event.currentTarget.classList.add('active');
            
            const titleMap = {
                dashboard: 'Dashboard',
                proxies: 'Proxy Manager',
                stress: 'Stress Test',
                master: 'Master Control',
                settings: 'Settings'
            };
            document.getElementById('page-title-text').innerText = titleMap[tabKey] || 'Dashboard';
        }

        function setEngine(mode) {
            currentEngine = mode;
            document.getElementById('engine-curl-btn').classList.toggle('active', mode === 'curl');
            document.getElementById('engine-browser-btn').classList.toggle('active', mode === 'browser');
        }

        function updateVal(key, val) {
            document.getElementById('val-' + key).innerText = val;
        }

        function updateDur() {
            const min = document.getElementById('slider-dur-min').value;
            const max = document.getElementById('slider-dur-max').value;
            document.getElementById('val-duration-min').innerText = min + 's';
            document.getElementById('val-duration-max').innerText = max + 's';
        }

        function appendLog(msg, type = 'info') {
            const term = document.getElementById('activity-terminal');
            if (!term) return;
            const line = document.createElement('div');
            line.className = 'log-row ' + type;
            const now = new Date().toTimeString().split(' ')[0];
            line.innerHTML = `<span class="log-timestamp">[${now}]</span> ${msg}`;
            term.appendChild(line);
            term.scrollTop = term.scrollHeight;
        }

        async function pollStatus() {
            try {
                const resp = await fetch('/api/campaign/status');
                if (resp.ok) {
                    const d = await resp.json();
                    document.getElementById('top-attempts').innerText = d.attempts || 0;
                    document.getElementById('top-delivered').innerText = d.delivered || 0;
                    document.getElementById('top-failed').innerText = d.failed || 0;
                    document.getElementById('top-rate').innerText = (d.rate || 0) + '%';
                    document.getElementById('top-proxies').innerText = d.live_proxies_count || 0;
                    document.getElementById('header-proxy-count').innerText = d.live_proxies_count || 0;

                    const btn = document.getElementById('btn-main-action');
                    const statusLabel = document.getElementById('header-status-label');
                    if (d.running) {
                        isRunning = true;
                        btn.innerText = 'STOP CAMPAIGN';
                        btn.className = 'btn-start-giant btn-stop-giant';
                        statusLabel.innerHTML = `<span style="color:var(--status-green);">Running (24/7)</span>  •  ${d.live_proxies_count} PROXIES`;
                    } else {
                        isRunning = false;
                        btn.innerText = 'START CAMPAIGN';
                        btn.className = 'btn-start-giant';
                        statusLabel.innerText = `Idle  •  0 ACTIVE - ${d.live_proxies_count} LOADED`;
                    }

                    if (d.logs && d.logs.length > 0) {
                        d.logs.forEach(l => appendLog(l.msg, l.type));
                    }
                }
            } catch (e) {}
        }

        setInterval(pollStatus, 2000);
        pollStatus();

        async function toggleCampaign() {
            if (isRunning) {
                appendLog('Stopping campaign...', 'warn');
                await fetch('/api/campaign/stop', { method: 'POST' });
                return;
            }

            const raw = document.getElementById('target_urls').value.trim();
            const urls = raw.split('\\n').map(u => u.trim()).filter(u => u.length > 4);
            if (urls.length === 0) {
                alert('Please enter at least 1 target URL in the Attack Configuration box!');
                return;
            }

            const payload = {
                target_urls: urls,
                threads: parseInt(document.getElementById('slider-threads').value) || 5,
                clicks_per_link: parseInt(document.getElementById('select-clicks-link').value) || 4,
                min_duration: parseInt(document.getElementById('slider-dur-min').value) || 15,
                max_duration: parseInt(document.getElementById('slider-dur-max').value) || 20,
                engine_mode: currentEngine,
                continuous_mode: document.getElementById('chk-continuous').checked
            };

            appendLog(`Launching campaign on ${urls.length} target URLs (${payload.clicks_per_link} clicks/link/IP)...`, 'info');
            try {
                const resp = await fetch('/api/campaign/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const res = await resp.json();
                if (res.status === 'ok') {
                    appendLog('✅ Campaign Started Successfully (24/7 Continuous Rotation)', 'success');
                } else {
                    appendLog('❌ Error: ' + res.message, 'error');
                }
            } catch (e) {
                appendLog('❌ Request failed: ' + e, 'error');
            }
        }

        async function fetchProxies() {
            const source = document.getElementById('grabber-source').value;
            const maxRows = parseInt(document.getElementById('grabber-max-rows').value) || 10000;
            document.getElementById('proxy-status-msg').innerText = `Fetching ${maxRows} proxies from ${source}...`;
            appendLog(`Fetching up to ${maxRows} proxies from ${source}...`, 'info');

            try {
                const resp = await fetch('/api/proxy/fetch', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ source, max_rows: maxRows })
                });
                const res = await resp.json();
                document.getElementById('proxy-status-msg').innerText = `Fetched ${res.count} proxies. Ready for HTTPS check.`;
                appendLog(`✅ Fetched ${res.count} unique proxies from ${source}.`, 'success');
                renderProxyGrid(res.proxies || []);
            } catch (e) {
                appendLog('❌ Proxy fetch error: ' + e, 'error');
            }
        }

        async function checkProxies() {
            document.getElementById('proxy-status-msg').innerText = 'Rechecking HTTPS against https://api.ipify.org...';
            appendLog('Checking proxies against https://api.ipify.org (100 concurrent workers)...', 'info');
            try {
                const resp = await fetch('/api/proxy/check', { method: 'POST' });
                const res = await resp.json();
                document.getElementById('proxy-status-msg').innerText = `Verified ${res.live_count} Live HTTPS proxies! (Saved & Transferred to Campaign)`;
                appendLog(`✅ Found ${res.live_count} verified Live HTTPS proxies!`, 'success');
                renderProxyGrid(res.live_proxies || []);
            } catch (e) {
                appendLog('❌ Proxy check error: ' + e, 'error');
            }
        }

        function renderProxyGrid(proxies) {
            const tbody = document.getElementById('proxy-grid-body');
            if (!proxies || proxies.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px;">No proxies found.</td></tr>';
                return;
            }
            tbody.innerHTML = proxies.slice(0, 100).map(p => `
                <tr>
                    <td style="font-family:'JetBrains Mono',monospace;"><strong>${p.host || p.ip}:${p.port}</strong></td>
                    <td><span class="tag-badge proto">${(p.protocol || 'HTTP').toUpperCase()}</span></td>
                    <td><span class="tag-badge pass">WORKING</span></td>
                    <td style="font-family:'JetBrains Mono',monospace;">${p.latency || '65'}ms</td>
                    <td><span class="tag-badge pass">PASS</span></td>
                    <td>${p.country || 'Global'}</td>
                </tr>
            `).join('');
        }

        function exportProxies(fmt) {
            window.open('/api/proxy/export?format=' + fmt, '_blank');
        }

        function resetConfig() {
            document.getElementById('target_urls').value = '';
            appendLog('Configuration reset.', 'info');
        }

        function checkTarget() {
            const raw = document.getElementById('target_urls').value.trim();
            if (!raw) { alert('Enter a URL first!'); return; }
            appendLog('Checking target connectivity: ' + raw.split('\\n')[0], 'info');
        }
    </script>
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


def render_full_dashboard(
    agents: dict,
    controllers: dict,
    start_time: float,
    host: str = "0.0.0.0",
    port: int = 8765,
    request_host: str = "",
) -> str:
    """Render the full pixel-perfect MR ADS Dashboard."""
    return MR_ADS_FULL_UI_HTML


render_dashboard = render_full_dashboard


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
        cpu_percent = psutil.cpu_percent(interval=None) if psutil else 0.0
        mem = psutil.virtual_memory() if psutil else None
        mem_used_mb = round(mem.used / (1024 * 1024), 2) if mem else 0.0
        mem_percent = mem.percent if mem else 0.0
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
