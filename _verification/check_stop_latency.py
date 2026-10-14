"""Measure how quickly traffic actually stops after the master says STOP."""
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

MASTER_PORT = 8801
TARGET_PORT = 8792
SECRET = "0123456789abcdef0123456789abcdef"

HITS = {"n": 0}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with LOCK:
            HITS["n"] += 1
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def hits():
    with LOCK:
        return HITS["n"]


target = ThreadingHTTPServer(("127.0.0.1", TARGET_PORT), Handler)
threading.Thread(target=target.serve_forever, daemon=True).start()

from core.master_server import MasterServer  # noqa: E402

master = MasterServer(host="127.0.0.1", port=MASTER_PORT, secret_key=SECRET)
master.start()
time.sleep(1.0)

log = open(str(_HERE / "stop_slave_stdout.txt"), "w", encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, "slave.py", "--master", "127.0.0.1:%d" % MASTER_PORT,
     "--name", "stop-slave", "--secret", SECRET, "--mode", "direct"],
    cwd=DEPLOY, stdout=log, stderr=subprocess.STDOUT, text=True,
)

deadline = time.time() + 25
while time.time() < deadline and master.slave_count < 1:
    time.sleep(0.5)
print("slaves connected:", master.slave_count)

master.start_traffic_on_slaves(
    target_url="http://127.0.0.1:%d/" % TARGET_PORT,
    threads=4, duration=0, min_view_time=0, max_view_time=1, block_direct=False,
)

time.sleep(6)
print("hits before stop:", hits())

master.stop_slaves()
stop_sent = time.time()

timeline = []
for _ in range(24):
    time.sleep(0.5)
    timeline.append((round(time.time() - stop_sent, 1), hits()))
print("seconds since STOP -> cumulative hits")
for moment, count in timeline:
    print("   %5.1fs  %d" % (moment, count))

settled = timeline[-1][1]
after_2s = next((c for t, c in timeline if t >= 2.0), None)
print("\nstop effectiveness:")
print("  hits at stop call        : %d" % (timeline[0][1] - (timeline[0][1] - hits())))
print("  hits 2s after stop       : %s" % after_2s)
print("  hits at end (12s)        : %d" % settled)
extra = settled - (after_2s if after_2s is not None else settled)
print("  extra hits after 2s      : %d" % extra)
print("  VERDICT: %s" % ("traffic stops promptly" if extra <= 2 else "TRAFFIC KEEPS RUNNING"))

master.stop_slaves()
time.sleep(1)
proc.terminate()
try:
    proc.wait(timeout=10)
except Exception:
    proc.kill()
log.close()
master.stop()
target.shutdown()
