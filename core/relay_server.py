import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

from aiohttp import WSMsgType, web

from .engine import AsyncTrafficEngine
from .models import EngineMode, ProxyConfig, TrafficConfig, TrafficStats
from .proxy_auto_pipeline import ProxyAutoPipeline
from .web_dashboard import (
    get_cluster_stats,
    get_health_status,
    render_full_dashboard,
)


class ClientType(Enum):
    """Type of connected client."""
    CONTROLLER = "controller"
    AGENT = "agent"


class MessageType(Enum):
    """WebSocket message types."""
    # Authentication
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RESPONSE = "auth_response"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"

    # Connection Status
    AGENT_CONNECTED = "agent_connected"
    AGENT_DISCONNECTED = "agent_disconnected"
    AGENT_LIST = "agent_list"

    # Routing
    COMMAND = "command"        # Controller -> Agent(s)
    RESULT = "result"          # Agent -> Controller
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"


@dataclass
class ConnectedClient:
    """Represents a connected client (Controller or Agent)."""
    client_id: str
    client_type: ClientType
    websocket: web.WebSocketResponse
    name: str = "Unknown"
    ip_address: str = "0.0.0.0"
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    session_token: str | None = None
    extra_info: dict = field(default_factory=dict)

    def __hash__(self):
        return hash(self.client_id)

    def __eq__(self, other):
        if isinstance(other, ConnectedClient):
            return self.client_id == other.client_id
        return False


class RelayServer:
    """
    WebSocket Relay Server.

    Manages connections from Controllers and Agents.
    Routes commands from Controllers to Agents.
    Routes results from Agents to Controllers.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        secret_key: str = "",
        heartbeat_interval: int = 30,
        timeout_seconds: int = 60,
        on_log = None,
        on_agent_connected = None,
        on_agent_disconnected = None,
    ):
        if not secret_key or len(secret_key) < 32:
            raise ValueError("secret_key must be at least 32 characters")

        self.host = host
        self.port = port
        self.secret_key = secret_key.encode()
        self.heartbeat_interval = heartbeat_interval
        self.timeout_seconds = timeout_seconds

        # Callbacks
        self.on_log = on_log
        self.on_agent_connected = on_agent_connected
        self.on_agent_disconnected = on_agent_disconnected

        # State
        self.start_time = time.time()
        self.controllers: dict[str, ConnectedClient] = {}  # controller_id -> client
        self.agents: dict[str, ConnectedClient] = {}  # agent_id -> client
        self.app: web.Application | None = None
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None

        # Interactive Campaign & Proxy Pipeline State
        self.activity_logs: list[dict] = []
        self.campaign_engine: AsyncTrafficEngine | None = None
        self.campaign_task: asyncio.Task | None = None
        self.campaign_stats = TrafficStats()
        self.proxy_pipeline = ProxyAutoPipeline(
            on_log=self._on_web_log,
            on_campaign_transfer=self._on_live_proxies_received,
        )

        self.logger = logging.getLogger(__name__)

    def _on_web_log(self, msg: str, log_type: str = "info"):
        """Record log entry for web dashboard activity console."""
        self._log(msg)
        self.activity_logs.append({
            "msg": msg,
            "type": log_type,
            "time": time.strftime("%H:%M:%S"),
        })
        if len(self.activity_logs) > 200:
            self.activity_logs.pop(0)

    def _on_live_proxies_received(self, live_proxies: list[ProxyConfig]):
        """Callback when background pipeline harvests live proxies."""
        if self.campaign_engine and self.campaign_engine.running:
            asyncio.create_task(self.campaign_engine.update_proxies(live_proxies))

    def _log(self, message: str, level: int = logging.INFO):
        """Log message and trigger callback."""
        self.logger.log(level, message)
        if self.on_log:
            try:
                self.on_log(message)
            except Exception:
                pass

    async def start(self):
        """Start the Relay Server."""
        if self._running:
            return

        self._log(f"Starting Relay Server on {self.host}:{self.port}")

        self.app = web.Application()
        self.app.router.add_get("/ws", self._handle_websocket)
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/healthz", self._handle_health)
        self.app.router.add_get("/api/status", self._handle_api_status)
        self.app.router.add_get("/api/agents", self._handle_api_agents)
        self.app.router.add_get("/api/slaves", self._handle_api_agents)
        self.app.router.add_get("/api/stats", self._handle_api_status)

        # Interactive Web Dashboard API Endpoints
        self.app.router.add_post("/api/campaign/start", self._handle_campaign_start)
        self.app.router.add_post("/api/campaign/stop", self._handle_campaign_stop)
        self.app.router.add_get("/api/campaign/status", self._handle_campaign_status)
        self.app.router.add_post("/api/proxy/fetch", self._handle_proxy_fetch)
        self.app.router.add_post("/api/proxy/check", self._handle_proxy_check)
        self.app.router.add_get("/api/proxy/list", self._handle_proxy_list)
        self.app.router.add_get("/api/proxy/export", self._handle_proxy_export)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())
        self._log(f"Relay Server running on ws://{self.host}:{self.port}/ws")
        self._log(f"Web Dashboard & Health API at http://{self.host}:{self.port}/")

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the Full Interactive Web Tool Dashboard."""
        request_host = request.host or f"{self.host}:{self.port}"
        html = render_full_dashboard(
            agents=self.agents,
            controllers=self.controllers,
            start_time=self.start_time,
            host=self.host,
            port=self.port,
            request_host=request_host,
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_campaign_start(self, request: web.Request) -> web.Response:
        """Start a campaign across multiple target URLs."""
        try:
            data = await request.json()
            target_urls = data.get("target_urls", [])
            if not target_urls:
                return web.json_response({"status": "error", "message": "No target URLs provided"}, status=400)

            threads = max(1, min(int(data.get("threads", 10)), 100))
            clicks_per_link = max(1, int(data.get("clicks_per_link", 4)))
            min_dur = max(1, int(data.get("min_duration", 2)))
            max_dur = max(min_dur, int(data.get("max_duration", 6)))
            continuous = bool(data.get("continuous_mode", True))

            config = TrafficConfig(
                target_url=target_urls[0],
                target_urls=target_urls,
                max_threads=threads,
                total_visits=0 if continuous else 500,
                min_duration=min_dur,
                max_duration=max_dur,
                clicks_per_link_per_ip=clicks_per_link,
                continuous_mode=continuous,
            )

            # Stop existing if running
            if self.campaign_engine and self.campaign_engine.running:
                self.campaign_engine.running = False
                if self.campaign_task:
                    self.campaign_task.cancel()

            # Ensure we have live proxies
            proxies = list(self.proxy_pipeline.live_proxies)

            self.campaign_engine = AsyncTrafficEngine(
                config=config,
                proxies=proxies,
                on_update=lambda s: setattr(self, "campaign_stats", s),
                on_log=lambda m: self._on_web_log(m, "info"),
            )

            self.campaign_task = asyncio.create_task(self.campaign_engine.run())
            self._on_web_log(f"Campaign launched: {len(target_urls)} URLs, {threads} threads, {clicks_per_link} clicks/link/IP.", "success")
            return web.json_response({"status": "ok", "message": "Campaign started", "urls": len(target_urls)})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def _handle_campaign_stop(self, request: web.Request) -> web.Response:
        """Stop active campaign."""
        if self.campaign_engine:
            self.campaign_engine.running = False
            self._on_web_log("Campaign stop signaled by user.", "warn")
        return web.json_response({"status": "ok", "message": "Campaign stopped"})

    async def _handle_campaign_status(self, request: web.Request) -> web.Response:
        """Return live campaign telemetry stats and activity logs."""
        engine = self.campaign_engine
        is_running = bool(engine and engine.running)
        stats = engine.stats if engine else self.campaign_stats

        delivered = stats.success
        failed = stats.failed
        attempts = stats.total_requests or (delivered + failed)
        rate = round((delivered / attempts * 100), 1) if attempts > 0 else 0.0

        logs = list(self.activity_logs[-20:])

        return web.json_response({
            "running": is_running,
            "attempts": attempts,
            "delivered": delivered,
            "failed": failed,
            "rate": rate,
            "live_proxies_count": len(self.proxy_pipeline.live_proxies),
            "logs": logs,
        })

    async def _handle_proxy_fetch(self, request: web.Request) -> web.Response:
        """Fetch candidates from MR ADS sources."""
        try:
            data = await request.json()
            max_rows = int(data.get("max_rows", 10000))
            self.proxy_pipeline.max_rows = max_rows
            candidates = await self.proxy_pipeline.fetch_all_sources()
            proxies_preview = [
                {"host": c["proxy"].split(":")[0], "port": int(c["proxy"].split(":")[1]), "protocol": c.get("protocol", "http")}
                for c in candidates[:100]
            ]
            return web.json_response({"status": "ok", "count": len(candidates), "proxies": proxies_preview})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def _handle_proxy_check(self, request: web.Request) -> web.Response:
        """Run HTTPS validation on fetched proxies."""
        try:
            live = await self.proxy_pipeline.run_grab_and_check_cycle()
            live_preview = [
                {"host": p.host, "port": p.port, "protocol": p.protocol, "latency": 120, "country": p.country or "Global"}
                for p in live[:100]
            ]
            return web.json_response({"status": "ok", "live_count": len(live), "live_proxies": live_preview})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def _handle_proxy_list(self, request: web.Request) -> web.Response:
        """Return current live proxy pool."""
        proxies = [
            {"host": p.host, "port": p.port, "protocol": p.protocol, "country": p.country}
            for p in self.proxy_pipeline.live_proxies
        ]
        return web.json_response({"count": len(proxies), "proxies": proxies})

    async def _handle_proxy_export(self, request: web.Request) -> web.Response:
        """Export live proxies as text or CSV."""
        fmt = request.query.get("format", "txt").lower()
        if fmt == "csv":
            lines = ["IP,Port,Protocol,Country"]
            for p in self.proxy_pipeline.live_proxies:
                lines.append(f"{p.host},{p.port},{p.protocol},{p.country}")
            return web.Response(text="\n".join(lines), content_type="text/csv", headers={"Content-Disposition": "attachment; filename=live_proxies.csv"})
        else:
            lines = [f"{p.protocol}://{p.host}:{p.port}" for p in self.proxy_pipeline.live_proxies]
            return web.Response(text="\n".join(lines), content_type="text/plain", headers={"Content-Disposition": "attachment; filename=live_proxies.txt"})

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Serve Railway / Docker HTTP Health Check."""
        data = get_health_status(self.agents, self.controllers, self.start_time)
        return web.json_response(data)

    async def _handle_api_status(self, request: web.Request) -> web.Response:
        """Serve JSON Cluster Telemetry & Health status."""
        data = get_cluster_stats(self.agents, self.controllers, self.start_time)
        return web.json_response(data)

    async def _handle_api_agents(self, request: web.Request) -> web.Response:
        """Serve JSON list of connected agents/slaves."""
        agents_list = [
            {
                "id": a.client_id,
                "name": a.name,
                "ip": a.ip_address,
                "connected_at": a.connected_at,
                "last_heartbeat": a.last_heartbeat,
                "extra_info": getattr(a, "extra_info", {}),
            }
            for a in self.agents.values()
        ]
        return web.json_response({"count": len(agents_list), "agents": agents_list})

    async def stop(self):
        """Stop the Relay Server."""
        if not self._running:
            return

        self._log("Stopping Relay Server...")
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Close all connections
        disconnect_tasks = []
        for client in list(self.controllers.values()):
            disconnect_tasks.append(client.websocket.close(code=1001, message=b"Server shutdown"))
        for client in list(self.agents.values()):
            disconnect_tasks.append(client.websocket.close(code=1001, message=b"Server shutdown"))

        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        self.controllers.clear()
        self.agents.clear()

        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

        self._log("Relay Server stopped")

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle incoming WebSocket connection."""
        ws = web.WebSocketResponse(heartbeat=self.heartbeat_interval)
        await ws.prepare(request)

        ip_address = request.remote or "unknown"
        temp_id = secrets.token_hex(8)

        self._log(f"New connection from {ip_address} (id: {temp_id})")

        client: ConnectedClient | None = None

        try:
            # Authenticate
            client = await self._authenticate(ws, ip_address, temp_id)
            if not client:
                await ws.close(code=4003, message=b"Authentication failed")
                return ws

            # Register client
            if client.client_type == ClientType.CONTROLLER:
                self.controllers[client.client_id] = client
                self._log(f"Controller connected: {client.name} ({client.client_id})")
                # Send current agent list to new controller
                await self._send_agent_list(client)
            else:
                self.agents[client.client_id] = client
                self._log(f"Agent connected: {client.name} ({client.client_id})")
                # Notify controllers
                await self._notify_controllers_agent_update(client, connected=True)
                if self.on_agent_connected:
                    self.on_agent_connected(client)

            # Message Loop
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(client, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    self._log(f"WebSocket error from {client.name}: {ws.exception()}", logging.ERROR)
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log(f"Error handling client {temp_id}: {str(e)}", logging.ERROR)
        finally:
            if client:
                await self._disconnect_client(client)
            else:
                await ws.close()

        return ws

    async def _authenticate(self, ws: web.WebSocketResponse, ip_address: str, temp_id: str) -> ConnectedClient | None:
        """Perform HMAC-SHA256 challenge-response authentication."""
        challenge = secrets.token_hex(32)

        try:
            # Send Challenge
            await ws.send_json({
                "type": MessageType.AUTH_CHALLENGE.value,
                "challenge": challenge
            })

            # Wait for Response
            msg = await ws.receive(timeout=10.0)
            if msg.type != WSMsgType.TEXT:
                return None

            data = json.loads(msg.data)

            # Validate structure
            if data.get("type") != MessageType.AUTH_RESPONSE.value:
                return None

            payload = data.get("payload", {})
            response_hmac = payload.get("response")
            client_type_str = payload.get("client_type", "").lower()
            client_name = payload.get("name", f"Client-{temp_id}")
            client_id = payload.get("id", temp_id) # Clients can suggest an ID (e.g. persistent agent ID)

            # Validate HMAC
            expected_hmac = hmac.new(
                self.secret_key,
                challenge.encode(),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(response_hmac or "", expected_hmac):
                self._log(f"Auth failed for {ip_address}: Invalid HMAC")
                await ws.send_json({"type": MessageType.AUTH_FAILURE.value, "reason": "Invalid credentials"})
                return None

            # Determine Client Type
            try:
                client_type = ClientType(client_type_str)
            except ValueError:
                self._log(f"Auth failed for {ip_address}: Invalid client type '{client_type_str}'")
                await ws.send_json({"type": MessageType.AUTH_FAILURE.value, "reason": "Invalid client type"})
                return None

            # Success
            session_token = secrets.token_hex(32)
            await ws.send_json({
                "type": MessageType.AUTH_SUCCESS.value,
                "session_token": session_token
            })

            return ConnectedClient(
                client_id=client_id,
                client_type=client_type,
                websocket=ws,
                name=client_name,
                ip_address=ip_address,
                session_token=session_token,
                extra_info=payload.get("extra_info", {})
            )

        except (json.JSONDecodeError, asyncio.TimeoutError) as e:
            self._log(f"Auth error for {ip_address}: {str(e)}")
            return None
        except Exception as e:
            self._log(f"Auth exception for {ip_address}: {str(e)}", logging.ERROR)
            return None

    async def _handle_message(self, client: ConnectedClient, data: str):
        """Route incoming messages based on client type."""
        try:
            message = json.loads(data)
            msg_type = message.get("type")
            payload = message.get("payload", {})

            # Heartbeat
            if msg_type == MessageType.HEARTBEAT.value:
                client.last_heartbeat = time.time()
                await client.websocket.send_json({
                    "type": MessageType.HEARTBEAT_ACK.value,
                    "timestamp": time.time()
                })
                return

            # Routing Logic
            if client.client_type == ClientType.CONTROLLER:
                await self._route_from_controller(client, msg_type, payload)
            elif client.client_type == ClientType.AGENT:
                await self._route_from_agent(client, msg_type, payload)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            self._log(f"Error processing message from {client.name}: {str(e)}", logging.ERROR)

    async def _route_from_controller(self, controller: ConnectedClient, msg_type: str, payload: dict):
        """Route commands from Controller to Agent(s)."""
        target_agent_id = payload.get("target_agent_id")

        # Construct message to forward
        forward_msg = {
            "type": msg_type,
            "payload": payload,
            "source_controller": controller.client_id,
            "timestamp": time.time()
        }

        if target_agent_id:
            # Unicast
            agent = self.agents.get(target_agent_id)
            if agent:
                await self._send_json_safe(agent, forward_msg)
            else:
                # Notify controller that agent is missing
                await self._send_json_safe(controller, {
                    "type": "error",
                    "payload": {"message": f"Agent {target_agent_id} not found"}
                })
        else:
            # Broadcast
            tasks = []
            for agent in self.agents.values():
                tasks.append(self._send_json_safe(agent, forward_msg))
            if tasks:
                await asyncio.gather(*tasks)

    async def _route_from_agent(self, agent: ConnectedClient, msg_type: str, payload: dict):
        """Route results/stats from Agent to All Controllers."""
        # Annotate with source agent info
        forward_msg = {
            "type": msg_type,
            "payload": payload,
            "source_agent_id": agent.client_id,
            "source_agent_name": agent.name,
            "timestamp": time.time()
        }

        tasks = []
        for controller in self.controllers.values():
            tasks.append(self._send_json_safe(controller, forward_msg))

        if tasks:
            await asyncio.gather(*tasks)

    async def _disconnect_client(self, client: ConnectedClient):
        """Cleanup disconnected client."""
        if client.client_type == ClientType.CONTROLLER:
            if client.client_id in self.controllers:
                del self.controllers[client.client_id]
                self._log(f"Controller disconnected: {client.name}")
        else:
            if client.client_id in self.agents:
                del self.agents[client.client_id]
                self._log(f"Agent disconnected: {client.name}")
                await self._notify_controllers_agent_update(client, connected=False)
                if self.on_agent_disconnected:
                    self.on_agent_disconnected(client)

    async def _notify_controllers_agent_update(self, agent: ConnectedClient, connected: bool):
        """Inform controllers about agent connection changes."""
        msg_type = MessageType.AGENT_CONNECTED.value if connected else MessageType.AGENT_DISCONNECTED.value
        msg = {
            "type": msg_type,
            "payload": {
                "agent_id": agent.client_id,
                "name": agent.name,
                "ip": agent.ip_address,
                "connected_at": agent.connected_at,
                "status": "online" if connected else "offline"
            }
        }

        tasks = [self._send_json_safe(c, msg) for c in self.controllers.values()]
        if tasks:
            await asyncio.gather(*tasks)

    async def _send_agent_list(self, controller: ConnectedClient):
        """Send list of all connected agents to a specific controller."""
        agents_list = [
            {
                "agent_id": a.client_id,
                "name": a.name,
                "ip": a.ip_address,
                "connected_at": a.connected_at,
                "status": "online"
            }
            for a in self.agents.values()
        ]

        await self._send_json_safe(controller, {
            "type": MessageType.AGENT_LIST.value,
            "payload": {"agents": agents_list}
        })

    async def _send_json_safe(self, client: ConnectedClient, data: dict):
        """Safely send JSON message to client."""
        try:
            await client.websocket.send_json(data)
        except Exception as e:
            self._log(f"Failed to send to {client.name}: {str(e)}", logging.DEBUG)

    async def _heartbeat_monitor(self):
        """Check for silent disconnections."""
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                now = time.time()

                # Check Agents
                timed_out_agents = [
                    a for a in self.agents.values()
                    if now - a.last_heartbeat > self.timeout_seconds
                ]
                for agent in timed_out_agents:
                    self._log(f"Agent timeout: {agent.name}")
                    await agent.websocket.close(code=1000, message=b"Heartbeat timeout")
                    # _disconnect_client will be called by the handler loop exiting

                # Check Controllers
                timed_out_controllers = [
                    c for c in self.controllers.values()
                    if now - c.last_heartbeat > self.timeout_seconds
                ]
                for controller in timed_out_controllers:
                    self._log(f"Controller timeout: {controller.name}")
                    await controller.websocket.close(code=1000, message=b"Heartbeat timeout")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log(f"Heartbeat monitor error: {str(e)}", logging.ERROR)
                await asyncio.sleep(5)

    def get_agents(self) -> list[dict]:
        """Get public info of all connected agents."""
        return [
            {
                "id": agent.client_id,
                "name": agent.name,
                "ip": agent.ip_address,
                "connected_at": agent.connected_at
            }
            for agent in self.agents.values()
        ]

