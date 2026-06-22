#!/usr/bin/env python3
"""Least-privilege GPU power-broker — reference implementation.

A tiny HTTPS endpoint **you** run next to your cloud account. It holds the real
(broad) provider credential server-side and exposes to Prax *only*:

    POST /power {"action": "on"}   -> start the ONE configured GPU instance
    POST /power {"action": "off"}  -> stop  the ONE configured GPU instance
    GET  /power                    -> {"state": "on"|"off"|"unknown"}

Everything else 404s. The instance ID is fixed in this process, so a leaked
client bearer can do nothing but flip *that* GPU on/off — not create, destroy,
resize, SSH, or read data. Pair it with Prax's `gpu_power` plugin (which holds
only the bearer + URL). See docs/guides/cloud-gpu.md.

Security posture (fail-closed):
  - Refuses to start without BROKER_TOKEN.
  - Refuses a non-loopback bind without TLS (cert+key).
  - Constant-time bearer check; bearer never logged.

Run (dry-run, no cloud, for testing):
  BROKER_TOKEN=$(openssl rand -hex 32) python3 broker.py        # http://127.0.0.1:8799

Run (AWS, TLS, on a tailnet/host):
  PROVIDER=aws AWS_REGION=us-east-1 BROKER_INSTANCE_ID=i-0abc... \
  BROKER_TOKEN=... BROKER_TLS_CERT=cert.pem BROKER_TLS_KEY=key.pem \
  BROKER_BIND=0.0.0.0 python3 broker.py
"""
from __future__ import annotations

import hmac
import json
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROVIDER = os.environ.get("PROVIDER", "dryrun").lower()
INSTANCE_ID = os.environ.get("BROKER_INSTANCE_ID", "")
TOKEN = os.environ.get("BROKER_TOKEN", "")
BIND = os.environ.get("BROKER_BIND", "127.0.0.1")
PORT = int(os.environ.get("BROKER_PORT", "8799"))
TLS_CERT = os.environ.get("BROKER_TLS_CERT", "")
TLS_KEY = os.environ.get("BROKER_TLS_KEY", "")

_dryrun_state = {"on": False}


# --------------------------------------------------------------------------- #
# Provider adapters — each does EXACTLY start / stop / state on ONE instance.
# --------------------------------------------------------------------------- #

def _aws(action: str) -> dict:
    import boto3  # noqa: PLC0415 — optional, only when PROVIDER=aws
    ec2 = boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    if action == "on":
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
        return {"action": "on", "instance": INSTANCE_ID}
    if action == "off":
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        return {"action": "off", "instance": INSTANCE_ID}
    r = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    s = r["Reservations"][0]["Instances"][0]["State"]["Name"]
    return {"state": "on" if s == "running" else "off", "raw": s}


def _runpod(action: str) -> dict:
    # RunPod has no start/stop-only token scope, which is exactly why the broker
    # exists: the broad RUNPOD_API_KEY stays here, never reaches Prax.
    import urllib.request  # noqa: PLC0415
    key = os.environ["RUNPOD_API_KEY"]
    verb = {"on": "resume", "off": "stop"}.get(action)
    if not verb:  # status
        return {"state": "unknown", "note": "implement GET via RunPod GraphQL"}
    req = urllib.request.Request(
        f"https://rest.runpod.io/v1/pods/{INSTANCE_ID}/{verb}",
        method="POST", headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return {"action": action, "http": resp.status}


def _dryrun(action: str) -> dict:
    if action in ("on", "off"):
        _dryrun_state["on"] = action == "on"
    return {"state": "on" if _dryrun_state["on"] else "off", "provider": "dryrun"}


_PROVIDERS = {"aws": _aws, "runpod": _runpod, "dryrun": _dryrun}


def power(action: str) -> dict:
    fn = _PROVIDERS.get(PROVIDER)
    if fn is None:
        raise ValueError(f"unknown PROVIDER {PROVIDER!r}")
    return fn(action)


# --------------------------------------------------------------------------- #
# HTTP handler — only POST/GET /power, bearer-gated, nothing else reachable.
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    def _authed(self) -> bool:
        hdr = self.headers.get("Authorization", "")
        scheme, _, presented = hdr.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(presented, TOKEN)

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") != "/power":
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            return self._send(200, power("status"))
        except Exception as e:
            return self._send(502, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/power":
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            action = (json.loads(self.rfile.read(n) or b"{}").get("action") or "").lower()
        except Exception:
            return self._send(400, {"error": "bad json"})
        if action not in ("on", "off"):
            return self._send(400, {"error": "action must be 'on' or 'off'"})
        try:
            return self._send(200, power(action))
        except Exception as e:
            return self._send(502, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):  # keep the bearer out of logs
        sys.stderr.write(f"[broker] {self.address_string()} {fmt % args}\n")


def main() -> int:
    if not TOKEN:
        sys.exit("FATAL: set BROKER_TOKEN (refusing to run unauthenticated).")
    if PROVIDER != "dryrun" and not INSTANCE_ID:
        sys.exit("FATAL: set BROKER_INSTANCE_ID for a real provider.")
    use_tls = bool(TLS_CERT and TLS_KEY)
    if BIND not in ("127.0.0.1", "localhost", "::1") and not use_tls:
        sys.exit("FATAL: refusing a non-loopback bind without TLS "
                 "(set BROKER_TLS_CERT + BROKER_TLS_KEY, or bind 127.0.0.1 behind a TLS proxy).")
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    scheme = "https" if use_tls else "http"
    sys.stderr.write(f"[broker] provider={PROVIDER} listening {scheme}://{BIND}:{PORT}/power\n")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
