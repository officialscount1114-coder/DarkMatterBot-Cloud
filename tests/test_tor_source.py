"""Tor as the IP source: control protocol, rotation rules, and the engine hook.

The rotation itself is Tor's ``SIGNAL NEWNYM`` on the control port - the same
mechanism the `ip-changer` / `auto-change-tor-ip` scripts use - but driven by the
traffic engine, with the exit IP verified after every rotation.
"""

import asyncio
import socket
import threading
import time

import pytest

from core.engine import AsyncTrafficEngine
from core.models import ProxyConfig, TrafficConfig
from core.tor_manager import (
    MIN_ROTATE_INTERVAL,
    TorController,
    TorError,
    TorManager,
    TorSettings,
    is_valid_port,
    looks_like_ip,
    parse_protocolinfo,
    quote_for_control,
)


class TestTorBundle:
    """Tor Browser is not required - the small tor daemon bundle is enough."""

    def test_this_platform_has_a_bundle(self):
        from core.tor_manager import tor_bundle_platform

        name = tor_bundle_platform()
        assert name, "no bundle name for this platform"
        assert "-" in name

    def test_bundle_url_points_at_the_tor_project(self):
        from core.tor_manager import tor_bundle_url

        url = tor_bundle_url("windows-x86_64")
        assert url.startswith("https://dist.torproject.org/torbrowser/")
        assert "tor-expert-bundle-windows-x86_64" in url
        assert url.endswith(".tar.gz")

    def test_unknown_platform_has_no_url(self):
        from core.tor_manager import tor_bundle_url

        assert tor_bundle_url("beos-m68k") == ""

    def test_finds_the_tor_binary_in_an_unpacked_bundle(self, tmp_path):
        import os

        from core.tor_manager import find_tor_in

        binary = "tor.exe" if os.name == "nt" else "tor"
        target = tmp_path / "tor-expert-bundle" / "tor" / binary
        target.parent.mkdir(parents=True)
        target.write_text("stub")
        assert find_tor_in(str(tmp_path)) == str(target)

    def test_missing_binary_reports_nothing(self, tmp_path):
        from core.tor_manager import find_tor_in

        assert find_tor_in(str(tmp_path)) == ""


class TestProtocolHelpers:
    def test_password_is_escaped_for_the_control_port(self):
        assert quote_for_control("simple") == '"simple"'
        # an unescaped quote breaks the AUTHENTICATE line
        assert quote_for_control('pa"ss') == '"pa\\"ss"'
        assert quote_for_control("back\\slash") == '"back\\\\slash"'

    def test_protocolinfo_is_parsed(self):
        reply = (
            "250-PROTOCOLINFO 1\n"
            '250-AUTH METHODS=COOKIE,SAFECOOKIE COOKIEFILE="/home/u/.tor/control_auth_cookie"\n'
            '250-VERSION Tor="0.4.8.10"'
        )
        info = parse_protocolinfo(reply)
        assert info["methods"] == ["COOKIE", "SAFECOOKIE"]
        assert info["cookiefile"].endswith("control_auth_cookie")
        assert info["version"] == "0.4.8.10"

    def test_protocolinfo_with_null_auth(self):
        info = parse_protocolinfo("250-AUTH METHODS=NULL")
        assert info["methods"] == ["NULL"]

    def test_port_and_ip_validation(self):
        assert is_valid_port(9050)
        assert not is_valid_port(0)
        assert not is_valid_port("abc")
        assert looks_like_ip("185.220.101.5")
        assert not looks_like_ip("<html>error</html>")

    def test_tor_settings_build_the_socks_url(self):
        assert TorSettings().socks_url == "socks5://127.0.0.1:9050"
        assert TorSettings(socks_port=9055).socks_url == "socks5://127.0.0.1:9055"

    def test_min_rotate_interval_respects_tor_rate_limit(self):
        assert TorSettings().min_rotate_seconds == MIN_ROTATE_INTERVAL
        assert MIN_ROTATE_INTERVAL >= 10


class FakeControlPort:
    """Stands in for Tor's control port, recording the commands it receives."""

    def __init__(self, auth: str = "NULL"):
        self.auth = auth
        self.commands: list[str] = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = ""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk.decode()
                while "\r\n" in buf:
                    line, buf = buf.split("\r\n", 1)
                    self.commands.append(line)
                    if line.startswith("PROTOCOLINFO"):
                        conn.sendall(
                            (
                                "250-PROTOCOLINFO 1\r\n"
                                f"250-AUTH METHODS={self.auth}\r\n"
                                '250-VERSION Tor="0.4.8.10"\r\n'
                                "250 OK\r\n"
                            ).encode()
                        )
                    else:
                        conn.sendall(b"250 OK\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._stop = True
        self.sock.close()


class TestControlClient:
    def test_authenticates_and_signals_newnym(self):
        server = FakeControlPort()
        try:
            controller = TorController(port=server.port)
            controller.connect()
            controller.signal_newnym()
            controller.close()
        finally:
            server.close()

        assert any(c.startswith("PROTOCOLINFO") for c in server.commands)
        assert any(c.startswith("AUTHENTICATE") for c in server.commands)
        assert "SIGNAL NEWNYM" in server.commands, "NEWNYM is what changes the IP"

    def test_connect_reports_a_clear_error_when_nothing_listens(self):
        controller = TorController(port=1)
        with pytest.raises(TorError):
            controller.connect()

    def test_errors_from_tor_are_raised(self):
        controller = TorController(port=1)
        controller._sock = None
        with pytest.raises(TorError):
            controller.command("SIGNAL NEWNYM")


class TestRotationRules:
    """The engine must rotate on the click batch, on the timer, and stop at the limit."""

    def make_engine(self, **overrides) -> AsyncTrafficEngine:
        values = {
            "target_url": "http://tor-target.test/",
            "max_threads": 1,
            "total_visits": 0,
            "min_duration": 0,
            "max_duration": 0,
            "clicks_per_ip": 5,
        }
        values.update(overrides)
        engine = AsyncTrafficEngine(TrafficConfig(**values), [])
        engine.on_log = lambda _message: None
        engine.running = True
        return engine

    @pytest.mark.asyncio
    async def test_rotation_happens_after_the_click_batch(self):
        engine = self.make_engine(clicks_per_ip=3)
        calls: list[int] = []

        async def hook():
            calls.append(1)

        engine.rotate_hook = hook
        engine._rotation_started = time.monotonic()

        async def one_click():
            # same order as the engine: decide first, then count this click
            await engine._maybe_rotate_ip()
            engine._clicks_since_rotation += 1

        for _ in range(3):
            await one_click()
        assert calls == [], "the first 3 clicks must stay on the same IP"

        await one_click()  # 4th click
        assert len(calls) == 1, "the 4th click must go out through a new IP"
        assert engine._clicks_since_rotation == 1

    @pytest.mark.asyncio
    async def test_rotation_happens_on_the_timer(self):
        engine = self.make_engine(clicks_per_ip=0, rotate_interval_seconds=1)
        calls: list[int] = []

        async def hook():
            calls.append(1)

        engine.rotate_hook = hook
        engine._rotation_started = time.monotonic() - 5  # interval already elapsed

        await engine._maybe_rotate_ip()
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_campaign_stops_at_the_ip_limit(self):
        engine = self.make_engine(clicks_per_ip=1, max_ip_rotations=2)
        logs: list[str] = []
        engine.on_log = logs.append

        async def hook():
            return None

        engine.rotate_hook = hook
        engine._rotation_started = time.monotonic()

        engine._clicks_since_rotation = 1
        await engine._maybe_rotate_ip()          # IP 2 of 2
        assert engine.running is True

        engine._clicks_since_rotation = 1
        await engine._maybe_rotate_ip()          # limit reached
        assert engine.running is False
        assert any("IP limit reached" in line for line in logs)

    @pytest.mark.asyncio
    async def test_no_hook_means_no_tor_rotation(self):
        engine = self.make_engine(clicks_per_ip=1)
        engine._clicks_since_rotation = 99
        await engine._maybe_rotate_ip()
        assert engine._ip_rotations == 0

    @pytest.mark.asyncio
    async def test_a_failing_hook_does_not_kill_the_campaign(self):
        engine = self.make_engine(clicks_per_ip=1)

        async def hook():
            raise RuntimeError("tor control port gone")

        engine.rotate_hook = hook
        engine._rotation_started = time.monotonic()
        engine._clicks_since_rotation = 1

        await engine._maybe_rotate_ip()
        assert engine.running is True


class TestTorManagerWithoutTor:
    def test_start_without_auto_start_explains_itself(self):
        """Whatever the machine looks like, the message must say what is wrong.

        The exact wording depends on what is already running: no Tor at all, a Tor
        without a control port, or a Tor that refuses authentication. All three
        must be explained rather than reported as a bare "Tor is not running".
        """
        manager = TorManager(TorSettings(auto_start=False, control_port=1))
        status = manager.start()
        assert status.running is False

        message = status.message.lower()
        assert message, "no explanation at all"
        assert any(
            phrase in message
            for phrase in (
                "not running",
                "no control port",
                "not installed",
                "refused us",
                "not be found",
            )
        ), message

    def test_rotate_without_tor_does_not_raise(self):
        manager = TorManager(TorSettings(control_port=1))
        assert manager.rotate(force=True) == ""

    def test_describe_mentions_the_state(self):
        manager = TorManager(TorSettings(control_port=1))
        assert "not running" in manager.describe().lower()

    @pytest.mark.asyncio
    async def test_engine_uses_a_socks5_tor_proxy(self):
        """The Tor source is injected as a plain socks5 proxy entry."""
        engine = AsyncTrafficEngine(
            TrafficConfig(
                target_url="http://x.test/",
                max_threads=1,
                total_visits=0,
                min_duration=0,
                max_duration=0,
            ),
            [ProxyConfig(host="127.0.0.1", port=9050, protocol="socks5")],
        )
        proxy = await engine._acquire_proxy()
        assert proxy.to_curl_cffi_format() == "socks5://127.0.0.1:9050"
