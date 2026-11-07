"""Stand-in for Strata's `python -m serve.server`, reproducing behaviour recorded on control 0.1.54/0.1.57.

Env knobs: FAKE_STRATA_LOAD (seconds before ready), FAKE_STRATA_FAIL (message: exit 1 while loading),
FAKE_STRATA_HANG_AFTER_STOP=1 (0.1.54: prints "stopped" but never exits), FAKE_STRATA_TPS (tokens/s).
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

p = argparse.ArgumentParser()
p.add_argument("--engine")
p.add_argument("--config")
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=8080)
p.add_argument("--parallel", type=int, default=1)
p.add_argument("--ready-file")
p.add_argument("--no-api-key", action="store_true")
args, _ = p.parse_known_args()

MODEL = "qwen3.8-flash-next"
CTX = 32768
if args.config:
    preset = json.load(open(args.config))
    MODEL = preset.get("model_name", MODEL)
    a = preset.get("args", [])
    CTX = int(a[a.index("--max-context") + 1]) if "--max-context" in a else CTX

state = {"loaded": False, "stopping": False, "inflight": 0}
lock = threading.Lock()
tps = float(os.environ.get("FAKE_STRATA_TPS", "200"))


def log(msg):
    print(f"[strata] {msg}", flush=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "max_context": CTX, "model": MODEL, "loaded": state["loaded"], "service": "strata"})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}] if state["loaded"] else []})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/__engine_died":  # test hook: the engine exits, the API stays up
            state["loaded"] = False
            return self._json(200, {"ok": True})
        if state["stopping"] or not state["loaded"]:
            return self._json(503, {"error": {"message": "stopping"}})
        n = int(body.get("max_tokens") or 8)
        with lock:
            state["inflight"] += 1
        try:
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i in range(n):
                    time.sleep(1 / tps)
                    chunk = {"choices": [{"index": 0, "delta": {"content": f"t{i} "}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                time.sleep(n / tps)
                self._json(200, {"id": uuid.uuid4().hex, "model": MODEL,
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with lock:
                state["inflight"] -= 1
        log(f"done: {n} tokens")


server = ThreadingHTTPServer((args.host, args.port), H)
server.daemon_threads = True


def on_stop(*_):
    if state["stopping"]:
        os._exit(130)  # second Ctrl+C: end at once
    state["stopping"] = True
    log("stopping (Ctrl+C again to end the engine at once) ...")

    def finish():
        while state["inflight"]:
            time.sleep(0.05)
        state["loaded"] = False
        server.shutdown()
        log("stopped")
        if os.environ.get("FAKE_STRATA_HANG_AFTER_STOP") == "1":
            while True:  # control 0.1.54: the process lingers after "stopped"
                time.sleep(1)
        os._exit(0)

    threading.Thread(target=finish, daemon=False).start()


signal.signal(signal.SIGINT, on_stop)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, on_stop)

threading.Thread(target=server.serve_forever, daemon=True).start()
log(f"loading {MODEL} (ctx {CTX})")
load = float(os.environ.get("FAKE_STRATA_LOAD", "0.5"))
steps = 5
for i in range(steps):
    time.sleep(load / steps)
    log(f"loading layers {int((i + 1) / steps * 100)}%")
    if os.environ.get("FAKE_STRATA_FAIL") and i == 2:
        log(f"error: {os.environ['FAKE_STRATA_FAIL']}")
        sys.exit(1)
state["loaded"] = True
if args.ready_file:
    with open(args.ready_file, "w") as f:
        json.dump({"api_state": "ready", "model_state": "ready", "pid": os.getpid(), "model_id": MODEL,
                   "instance_id": uuid.uuid4().hex, "bound_port": args.port, "schema_version": 1}, f)
log(f"ready: http://{args.host}:{args.port}/v1")
while True:
    time.sleep(1)
