#!/usr/bin/env python3
"""Connectivity smoke test for a brought-up Prax stack.

Verifies that everything is not just *running* but actually *connected* — the
cross-service wiring a fresh clone needs and that has regressed before
(TeamWork serving its SPA, TeamWork→Prax proxy, TeamWork→sandbox panels, Prax→
sandbox CDP, Prax→memory). Run it after `make run-local-all[-dev]`:

    make smoke            # or: python scripts/smoke_test.py

Exit code 0 = all CRITICAL checks passed. Non-zero = at least one critical
failure (with a diagnostic). Optional services (sandbox/TeamWork) that aren't
configured are reported as skipped, not failed.

Dependency-free (stdlib only) so it runs in any fresh environment. Ports are
overridable via env: PRAX_PORT, TEAMWORK_PORT, TEAMWORK_DEV_PORT, QDRANT_PORT,
NEO4J_BOLT_PORT, SANDBOX_OPENCODE_PORT, SANDBOX_CDP_PORT, SANDBOX_NOVNC_PORT.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import time
import urllib.request

H = "127.0.0.1"
PRAX = int(os.environ.get("PRAX_PORT", "5001"))
TW = int(os.environ.get("TEAMWORK_PORT", "8000"))
TW_DEV = int(os.environ.get("TEAMWORK_DEV_PORT", "5173"))
QDRANT = int(os.environ.get("QDRANT_PORT", "6333"))
NEO4J = int(os.environ.get("NEO4J_BOLT_PORT", "7687"))
SB_OPENCODE = int(os.environ.get("SANDBOX_OPENCODE_PORT", "4096"))
SB_CDP = int(os.environ.get("SANDBOX_CDP_PORT", "9223"))
SB_NOVNC = int(os.environ.get("SANDBOX_NOVNC_PORT", "6080"))
# Observability stack (LGTM). Grafana publishes on host :3002 (the tailscale
# sidecar / serve maps the tailnet :3001 to it); the others on their native ports.
GRAFANA = int(os.environ.get("GRAFANA_PORT", "3002"))
LOKI = int(os.environ.get("LOKI_PORT", "3100"))
TEMPO_QUERY = int(os.environ.get("TEMPO_QUERY_PORT", "3200"))
TEMPO_OTLP = int(os.environ.get("TEMPO_OTLP_HTTP_PORT", "4318"))
PROM = int(os.environ.get("PROMETHEUS_PORT", "9090"))

GREEN, RED, YEL, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
_results: list[tuple[str, str, str]] = []  # (status, name, detail)


def _get(url: str, timeout: float = 6.0, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (localhost only)


def _http_ok(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        r = _get(url, timeout)
        return (200 <= r.status < 400, f"HTTP {r.status}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:60]}")


def _json(url: str, timeout: float = 6.0, headers: dict | None = None):
    return json.loads(_get(url, timeout, headers).read().decode())


def _post(url: str, body: bytes, headers: dict | None = None, timeout: float = 6.0):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (localhost only)


def _otlp_trace_roundtrip() -> tuple[bool, str]:
    """Push a synthetic OTLP span to Tempo, then read it back by trace id.

    Proves the trace pipe end-to-end (OTLP ingest :4318 → Tempo store → query
    :3200) without needing a real agent turn (which would need provider keys).
    Tempo accepts OTLP/JSON when Content-Type is application/json.
    """
    trace_id = os.urandom(16).hex()  # 32 hex chars
    span_id = os.urandom(8).hex()    # 16 hex chars
    now_ns = int(time.time() * 1e9)
    payload = {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "prax-smoke"}}
            ]},
            "scopeSpans": [{
                "spans": [{
                    "traceId": trace_id, "spanId": span_id,
                    "name": "smoke-roundtrip", "kind": 1,
                    "startTimeUnixNano": str(now_ns - 1_000_000),
                    "endTimeUnixNano": str(now_ns),
                }],
            }],
        }],
    }
    try:
        r = _post(f"http://{H}:{TEMPO_OTLP}/v1/traces",
                  json.dumps(payload).encode(),
                  {"Content-Type": "application/json"})
        if not (200 <= r.status < 300):
            return (False, f"OTLP ingest HTTP {r.status}")
    except Exception as e:
        return (False, f"OTLP ingest failed: {type(e).__name__}: {str(e)[:50]}")
    # Read it back — Tempo needs a moment to make the span queryable. Note its
    # TraceByID response encodes the trace id as BASE64 (not the hex used in the
    # request/URL path), so match on that to confirm it's the exact span we sent.
    want_b64 = base64.b64encode(bytes.fromhex(trace_id)).decode()
    for _ in range(15):
        try:
            r = _get(f"http://{H}:{TEMPO_QUERY}/api/traces/{trace_id}", timeout=4)
            if r.status == 200 and want_b64 in r.read().decode(errors="replace"):
                return (True, f"span {trace_id[:8]}… ingested + queryable")
        except Exception:
            pass
        time.sleep(1)
    return (False, f"span {trace_id[:8]}… ingested but not queryable after 15s")


def _tcp_open(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ws_upgrade(host: str, port: int, path: str, origin: str, hosthdr: str,
                timeout: float = 6.0) -> tuple[bool, str]:
    """Raw WebSocket upgrade; return (got_101, first-line)."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = "\r\n".join([
            f"GET {path} HTTP/1.1", f"Host: {hosthdr}",
            "Upgrade: websocket", "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
            f"Origin: {origin}",
        ]) + "\r\n\r\n"
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\r\n", 1)[0].decode(errors="replace")
        s.close()
        return ("101" in line, line[:60])
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:50]}")


def check(name: str, ok: bool, detail: str = "", *, critical: bool = True) -> bool:
    status = "PASS" if ok else ("FAIL" if critical else "WARN")
    _results.append((status, name, detail))
    icon = {"PASS": f"{GREEN}✓{RST}", "FAIL": f"{RED}✗{RST}", "WARN": f"{YEL}⚠{RST}"}[status]
    print(f"  {icon} {name:<42} {DIM}{detail}{RST}")
    return ok


def skip(name: str, detail: str) -> None:
    _results.append(("SKIP", name, detail))
    print(f"  {DIM}- {name:<42} skipped: {detail}{RST}")


def main() -> int:  # noqa: C901 — a flat list of independent checks reads clearer than helpers
    print("\nPrax stack connectivity smoke test\n" + "=" * 50)

    # --- Core: Prax + memory ------------------------------------------------
    print("\nCore (Prax + memory):")
    prax_up, d = _http_ok(f"http://{H}:{PRAX}/health")
    check("Prax /health", prax_up, d)
    check("Qdrant (memory vector store)", _tcp_open(H, QDRANT), f":{QDRANT}", critical=False)
    check("Neo4j (memory graph) bolt", _tcp_open(H, NEO4J), f":{NEO4J}", critical=False)

    # Prax's own view of deployment + sandbox (the deployment-awareness surface).
    dep = {}
    if prax_up:
        try:
            dep = _json(f"http://{H}:{PRAX}/teamwork/deployment")
            check("Prax /teamwork/deployment", bool(dep.get("available")),
                  f"effective_base_url={dep.get('effective_base_url')}")
        except Exception as e:
            check("Prax /teamwork/deployment", False, str(e)[:60])

    # --- Sandbox (optional) -------------------------------------------------
    print("\nSandbox (browser/terminal/desktop — if enabled):")
    sb_cdp_http = _tcp_open(H, SB_CDP)
    if not sb_cdp_http and not _tcp_open(H, SB_OPENCODE):
        skip("sandbox", "no sandbox ports open (SANDBOX_ENABLED=false or not started)")
    else:
        check("Sandbox OpenCode /global/health",
              _http_ok(f"http://{H}:{SB_OPENCODE}/global/health")[0], f":{SB_OPENCODE}")
        # CDP reachable + a real target.
        cdp_ws_path = ""
        try:
            ver = _json(f"http://{H}:{SB_CDP}/json/version", headers={"Host": f"{H}:{SB_CDP}"})
            cdp_ws_path = "/" + (ver.get("webSocketDebuggerUrl", "").split("://", 1)[-1].split("/", 1)[-1])
            check("Sandbox CDP /json/version", True, ver.get("Browser", "?"))
        except Exception as e:
            check("Sandbox CDP /json/version", False, str(e)[:60])
        # CDP WS upgrade — the browser-panel regression (Chrome --remote-allow-origins).
        if cdp_ws_path:
            ok, line = _ws_upgrade(H, SB_CDP, cdp_ws_path, "http://127.0.0.1:9222", f"{H}:{SB_CDP}")
            check("Sandbox CDP WebSocket upgrade", ok, line)
        # Desktop noVNC assets + RFB handshake — the desktop-panel regression.
        check("Sandbox desktop noVNC assets", _http_ok(f"http://{H}:{SB_NOVNC}/vnc.html")[0], f":{SB_NOVNC}",
              critical=False)
        rfb_ok, rfb = _ws_upgrade(H, SB_NOVNC, "/websockify", "http://127.0.0.1", f"{H}:{SB_NOVNC}")
        check("Sandbox desktop websockify upgrade", rfb_ok, rfb, critical=False)

    # --- TeamWork (optional) + cross-connections ---------------------------
    print("\nTeamWork (web UI) + cross-connections:")
    tw_dev = _tcp_open(H, TW_DEV)
    tw_port = TW_DEV if tw_dev else TW
    if not _tcp_open(H, tw_port):
        skip("teamwork", "no TeamWork port open (TEAMWORK_ENABLED=false or not started)")
    else:
        mode = "dev :5173" if tw_dev else "prod :8000"
        check(f"TeamWork API /health ({mode})", _http_ok(f"http://{H}:{tw_port}/health")[0]
              if not tw_dev else _http_ok(f"http://{H}:{TW}/health")[0], f":{TW}")
        # The SPA must actually serve (the "frontend not built → 404" regression).
        try:
            root = _get(f"http://{H}:{tw_port}/", 6)
            body = root.read(2048).decode(errors="replace").lower()
            check("TeamWork SPA serves (not 404)", root.status == 200 and ("<!doctype html" in body or "<html" in body),
                  f"HTTP {root.status}")
        except Exception as e:
            check("TeamWork SPA serves (not 404)", False, str(e)[:60])
        # TeamWork → Prax proxy (the /api/prax/* + observability cross-link).
        try:
            d = _json(f"http://{H}:{tw_port}/api/prax/deployment")
            check("TeamWork → Prax proxy (/api/prax/deployment)", bool(d.get("available")),
                  f"via_proxy effective_base_url={d.get('effective_base_url')}")
        except Exception as e:
            check("TeamWork → Prax proxy (/api/prax/deployment)", False, str(e)[:60])
        check("TeamWork → Prax (/api/observability/config)",
              _http_ok(f"http://{H}:{tw_port}/api/observability/config")[0], "", critical=False)

    # --- Observability (optional) + data-flow -------------------------------
    # Not just "is it up" — prove telemetry is actually arriving: logs in Loki,
    # the Prax metrics target UP in Prometheus, and a trace round-trips Tempo.
    print("\nObservability (LGTM stack — if enabled) + data flow:")
    if not _tcp_open(H, GRAFANA):
        skip("observability", f"Grafana :{GRAFANA} not open (stack not started — "
             "'make run-local-all' brings it up)")
    else:
        gfok, gfd = _http_ok(f"http://{H}:{GRAFANA}/api/health")
        check("Grafana /api/health", gfok, gfd)
        # Datasources must be provisioned with the UIDs the Explore deep-links use.
        try:
            ds = _json(f"http://{H}:{GRAFANA}/api/datasources")
            uids = {d.get("uid") for d in ds}
            check("Grafana datasources provisioned (loki/tempo/prometheus)",
                  {"loki", "tempo", "prometheus"} <= uids, ",".join(sorted(uids)))
        except Exception as e:
            check("Grafana datasources provisioned (loki/tempo/prometheus)", False, str(e)[:60])
        # Loki — native host logs are flowing (promtail tails .local-run/*.log).
        try:
            vals = _json(f"http://{H}:{LOKI}/loki/api/v1/label/service/values").get("data", [])
            check("Loki receiving native logs (service=prax)", "prax" in vals,
                  f"services={','.join(vals[:6])}" if vals else "no service labels yet")
        except Exception as e:
            check("Loki receiving native logs (service=prax)", False, str(e)[:60])
        # Prometheus — the Prax metrics scrape target is UP (host-gateway bridge).
        try:
            tgts = _json(f"http://{H}:{PROM}/api/v1/targets").get("data", {}).get("activeTargets", [])
            prax_up = any(t.get("labels", {}).get("job") == "prax" and t.get("health") == "up"
                          for t in tgts)
            detail = next((f"{t['scrapeUrl']} {t['health']}" for t in tgts
                           if t.get("labels", {}).get("job") == "prax" and t.get("health") == "up"),
                          "no healthy prax target")
            check("Prometheus scraping Prax /metrics (target up)", prax_up, detail)
        except Exception as e:
            check("Prometheus scraping Prax /metrics (target up)", False, str(e)[:60])
        # Tempo — full trace pipe: OTLP ingest → store → query round-trip.
        ok, detail = _otlp_trace_roundtrip()
        check("Tempo trace ingest+query round-trip", ok, detail)

    # --- Summary ------------------------------------------------------------
    fails = [r for r in _results if r[0] == "FAIL"]
    warns = [r for r in _results if r[0] == "WARN"]
    npass = sum(1 for r in _results if r[0] == "PASS")
    print("\n" + "=" * 50)
    print(f"{npass} passed, {len(fails)} failed (critical), {len(warns)} warnings, "
          f"{sum(1 for r in _results if r[0] == 'SKIP')} skipped")
    if fails:
        print(f"{RED}SMOKE TEST FAILED{RST} — the stack is up but not fully connected:")
        for _, name, detail in fails:
            print(f"  {RED}✗{RST} {name}: {detail}")
        return 1
    print(f"{GREEN}SMOKE TEST PASSED{RST} — everything is connected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
