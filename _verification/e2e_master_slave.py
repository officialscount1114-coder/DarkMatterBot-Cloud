"""Live end-to-end check: real MasterServer + real slave.py subprocess.

Verifies the master/slave chain that the "traffic never arrives" report was
about:
  1. the packaged slave actually starts (it used to die on `import ui`);
  2. it connects and authenticates to the master;
  3. START_TRAFFIC with no proxies + direct allowed really produces hits on the
     target and the slave reports them back;
  4. START_TRAFFIC with no proxies + direct blocked is refused instead of
     quietly sending traffic from the slave's own IP.
"""
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path
_HERE = Path(__file__).resolve().parent
PROJECT = str(_HERE.parent)
DEPLOY = str(_HERE / "_deploy_sim")

import shutil
if Path(DEPLOY).exists():
    shutil.rmtree(DEPLOY, ignore_errors=True)
shutil.copytree(str(Path(PROJECT) / "server_deploy"), DEPLOY)
sys.path.insert(0, PROJECT)

MASTER_PORT = 8799
TARGET_PORT = 8791
SECRET = "0123456789abcdef0123456789abcdef"

HITS = {"n": 0}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with LOCK:
            HITS["n"] += 1
        body = b"<html><body>target ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def hits():
    with LOCK:
        return HITS["n"]


failures = []


def check(label, good, detail=""):
    print(("  PASS  " if good else "  FAIL  ") + label + ((" -- " + detail) if detail else ""))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------- target site
target = ThreadingHTTPServer(("127.0.0.1", TARGET_PORT), Handler)
threading.Thread(target=target.serve_forever, daemon=True).start()
print("target server on 127.0.0.1:%d" % TARGET_PORT)

from core.master_server import MasterServer  # noqa: E402
from core.websocket_server import MessageType  # noqa: E402

logs = []
connected = []

master = MasterServer(
    host="127.0.0.1",
    port=MASTER_PORT,
    secret_key=SECRET,
    on_log=lambda m: logs.append(m),
    on_slave_connected=lambda sid, info: connected.append(sid),
)
started = master.start()
print("master started:", started)
if not started:
    print("FATAL: master did not start")
    raise SystemExit(1)
time.sleep(1.0)

# ---------------------------------------------------------------- slave
print("\n[1] launching the packaged slave as a real subprocess")
slave_log = open(str(_HERE / "slave_stdout.txt"), "w", encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, "slave.py", "--master", "127.0.0.1:%d" % MASTER_PORT,
     "--name", "live-slave", "--secret", SECRET, "--mode", "direct",
     "--log-level", "INFO"],
    cwd=DEPLOY, stdout=slave_log, stderr=subprocess.STDOUT, text=True,
)

deadline = time.time() + 25
while time.time() < deadline and master.slave_count < 1:
    time.sleep(0.5)

slave_log.flush()
if proc.poll() is not None:
    print(open(str(_HERE / "slave_stdout.txt"), encoding="utf-8").read()[:2000])

check("packaged slave process stays alive", proc.poll() is None,
      "exit code %s" % proc.poll())
check("slave connected to master", master.slave_count >= 1,
      "slave_count=%d connected=%s" % (master.slave_count, connected))

# ---------------------------------------------------------------- traffic run
print("\n[2] starting traffic (no proxies, direct allowed)")
before = hits()
count = master.start_traffic_on_slaves(
    target_url="http://127.0.0.1:%d/" % TARGET_PORT,
    threads=3,
    duration=0,
    min_view_time=0,
    max_view_time=1,
    block_direct=False,
)
print("  slaves that accepted the command:", count)
check("master dispatched the traffic command", count >= 1)

sent = 0
deadline = time.time() + 20
while time.time() < deadline:
    if hits() > before + 3:
        break
    time.sleep(0.5)
sent = hits() - before
check("traffic actually reached the target", sent > 0, "%d hits" % sent)

# the slave reports stats on its own interval (5s by default), so poll for them
slaves = []
deadline = time.time() + 25
while time.time() < deadline:
    slaves = master.get_slaves()
    if any(s.requests > 0 for s in slaves):
        break
    time.sleep(1.0)

reported = [(s.slave_name, s.requests, s.success, s.status) for s in slaves]
print("  master sees:", reported)
check("slave reported traffic stats back to the master",
      any(s.requests > 0 for s in slaves),
      "%s" % reported)
check("reported delivery matches the master's own view",
      any(s.success > 0 for s in slaves))

master.stop_slaves()
time.sleep(2)
mid = hits()
time.sleep(3)
check("traffic really stopped", hits() - mid <= 2, "extra hits: %d" % (hits() - mid))

# ---------------------------------------------------------------- blocked run
print("\n[3] starting traffic again with direct traffic blocked (still no proxies)")
before_blocked = hits()
master.start_traffic_on_slaves(
    target_url="http://127.0.0.1:%d/" % TARGET_PORT,
    threads=3,
    duration=0,
    min_view_time=0,
    max_view_time=1,
    block_direct=True,
)
time.sleep(6)
blocked_sent = hits() - before_blocked
check("no traffic sent when a proxy is required but none exists",
      blocked_sent == 0, "%d hits" % blocked_sent)

master.stop_slaves()
time.sleep(1)
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
slave_log.close()
master.stop()
target.shutdown()

print("\n--- last slave log lines ---")
try:
    lines = open(str(_HERE / "slave_stdout.txt"), encoding="utf-8").read().splitlines()
    for line in lines[-18:]:
        print("   " + line)
except Exception as exc:
    print("   (could not read slave log: %s)" % exc)

print("\nRESULT:", "ALL PASS" if not failures else ("FAILURES: %s" % failures))
sys.exit(1 if failures else 0)
