"""Deployment / network-reachability detection.

Lets Prax answer "how do people reach me?" from ground truth instead of guessing
(e.g. telling a Discord user the wrong URL, or assuming `localhost`). Detection
combines:
- the **tailscale CLI** (native-host deployments) — `tailscale status --json`
  gives the MagicDNS hostname + backend state;
- **env signals** — `TS_HOSTNAME` (set for the docker tailscale *sidecar*, which
  the prax process can't query directly), `NGROK_URL`, and `TEAMWORK_BASE_URL`.

Everything degrades to "local only" when nothing is detected. CLI calls are
cached (they rarely change) and time-bounded so they never stall a turn.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0
_cache: dict[str, tuple[object, float]] = {}


def clear_cache() -> None:
    _cache.clear()


def _cached(key: str, producer):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[1] > now:
        return hit[0]
    val = producer()
    _cache[key] = (val, now + _CACHE_TTL)
    return val


def _run(cmd: list[str], timeout: float = 3.0):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def tailscale_status() -> dict:
    """Parsed `tailscale status --json`: availability, MagicDNS hostname, IPs."""
    def _produce() -> dict:
        if not shutil.which("tailscale"):
            return {"available": False, "reason": "tailscale CLI not installed"}
        r = _run(["tailscale", "status", "--json"])
        if not r or r.returncode != 0 or not r.stdout:
            return {"available": False, "reason": "tailscale not running or not logged in"}
        try:
            data = json.loads(r.stdout)
        except Exception:
            return {"available": False, "reason": "could not parse tailscale status"}
        self_node = data.get("Self") or {}
        hostname = (self_node.get("DNSName") or "").rstrip(".")
        state = data.get("BackendState", "")
        return {
            "available": state == "Running" and bool(hostname),
            "backend_state": state,
            "hostname": hostname,
            "ips": self_node.get("TailscaleIPs", []),
            "tailnet": (data.get("MagicDNSSuffix") or "").rstrip("."),
        }
    return _cached("status", _produce)


def tailscale_serve_mappings() -> list[str]:
    """Best-effort `tailscale serve status` lines (may be empty without sudo)."""
    def _produce() -> list[str]:
        if not shutil.which("tailscale"):
            return []
        r = _run(["tailscale", "serve", "status"])
        if not r or r.returncode != 0 or not r.stdout:
            return []
        return [ln.strip() for ln in r.stdout.splitlines()
                if ln.strip() and ("proxy" in ln or ln.strip().startswith(("https://", "|--")))]
    return _cached("serve", _produce)


def _is_local_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return (not u) or u.startswith(("http://localhost", "http://127.", "http://0.0.0.0", "http://[::1]"))


def get_deployment_info() -> dict:
    """Aggregate reachability facts + advisories."""
    from prax.settings import settings

    ts = tailscale_status()
    ts_hostname_env = (os.environ.get("TS_HOSTNAME") or "").strip()
    ngrok = (getattr(settings, "ngrok_url", None) or "").strip()
    teamwork_base = (getattr(settings, "teamwork_base_url", None) or "").strip()
    in_docker = bool(getattr(settings, "running_in_docker", False))

    public_base_url = None
    public_via = None
    if ngrok:
        public_base_url, public_via = ngrok, "ngrok"
    elif ts.get("available") and ts.get("hostname"):
        public_base_url, public_via = f"https://{ts['hostname']}", "tailscale"
    elif ts_hostname_env:
        public_via = "tailscale-sidecar"  # full FQDN not visible to this process

    # Effective base URL Prax should use when building shareable links:
    #   1. auto-detect on and a public URL found → use it;
    #   2. else the configured TEAMWORK_BASE_URL (may be localhost).
    #
    # Detection outranks the configured value on purpose. The reverse ordering —
    # "an explicit non-local TEAMWORK_BASE_URL always wins" — reads like respect
    # for operator intent, but it loses to the thing that actually happens: a
    # deployment moves and .env still names the old host. That URL is well-formed
    # and not local, so it won every time and Prax sent people links to a machine
    # it is not running on. Detection reads THIS machine's serve mappings and so
    # cannot go stale; when the two disagree, the configured one has drifted.
    #
    # PUBLIC_URL_AUTODETECT=false pins the configured value, for a URL detection
    # cannot see (a custom domain in front of the service).
    autodetect = bool(getattr(settings, "public_url_autodetect", True))
    if autodetect and public_base_url:
        effective_base_url, effective_via = public_base_url, f"auto:{public_via}"
    else:
        effective_base_url = teamwork_base or None
        effective_via = "config" if teamwork_base else None

    advisories: list[str] = []
    behind_proxy = bool(ts.get("available") or ts_hostname_env or ngrok)
    # Only warn when the URL we'd actually use is still local despite a proxy —
    # i.e. auto-detect couldn't help (sidecar with no visible FQDN) or is off.
    if behind_proxy and _is_local_url(effective_base_url):
        if public_base_url and not autodetect:
            advisories.append(
                "TEAMWORK_BASE_URL points at localhost, so links shared off-network "
                f"(Discord/SMS) aren't reachable. Set it to {public_base_url}, or set "
                "PUBLIC_URL_AUTODETECT=true to use the detected public URL automatically."
            )
        else:
            hint = f"(e.g. {public_base_url})" if public_base_url else "(your public tailnet HTTPS URL)"
            advisories.append(
                "TEAMWORK_BASE_URL points at localhost and no public URL could be "
                f"auto-detected, so off-network links (Discord/SMS) aren't reachable. "
                f"Set TEAMWORK_BASE_URL to your public URL {hint} and restart."
            )

    return {
        "in_docker": in_docker,
        "tailscale": ts,
        "ts_hostname_env": ts_hostname_env or None,
        "ngrok_url": ngrok or None,
        "teamwork_base_url": teamwork_base or None,
        "public_base_url": public_base_url,
        "public_via": public_via,
        "effective_base_url": effective_base_url,
        "effective_via": effective_via,
        "autodetect": autodetect,
        "serve_mappings": tailscale_serve_mappings() if ts.get("available") else [],
        "advisories": advisories,
    }


def effective_base_url() -> str:
    """The base URL Prax should use when building shareable links.

    Auto-derives the public Tailscale/ngrok URL when ``TEAMWORK_BASE_URL`` is
    unset/localhost and ``PUBLIC_URL_AUTODETECT`` is on (the default), so a
    Tailscale deploy works without editing ``.env``. Always returns a trailing-
    slash-stripped string; falls back to the configured value on any error.
    """
    from prax.settings import settings
    configured = (getattr(settings, "teamwork_base_url", "") or "").rstrip("/")

    # Auto-detect off → the configured value, as written.
    if not getattr(settings, "public_url_autodetect", True):
        return configured

    # Otherwise DETECTION WINS over the configured value.
    #
    # It used to be the other way around: any explicit non-local
    # TEAMWORK_BASE_URL took precedence, and detection only filled in for an
    # empty or localhost setting. That is wrong for the case that actually
    # happens — a deployment moves to a new host and `.env` still names the old
    # one. The old URL is perfectly well-formed and not local, so it won every
    # time, and Prax cheerfully sent people links to a machine it is not running
    # on. A stale value is worse than no value, because nothing looks broken.
    #
    # Detection reads THIS machine's serve mappings, so it cannot be stale. When
    # it disagrees with the configured value, the configured value is the one
    # that has drifted.
    #
    # Deliberately pinning a URL detection cannot see — a custom domain in front
    # of the service, say — is what PUBLIC_URL_AUTODETECT=false is for, and the
    # log line below names it so the escape hatch is discoverable at the moment
    # it matters.
    try:
        url = (get_deployment_info().get("effective_base_url") or "").rstrip("/")
        if url:
            if configured and configured != url:
                logger.info(
                    "using the detected public URL %s rather than the configured "
                    "TEAMWORK_BASE_URL %s — set PUBLIC_URL_AUTODETECT=false to "
                    "force the configured value", url, configured)
            return url
    except Exception:
        logger.debug("effective_base_url detection failed; using configured value", exc_info=True)
    return configured


def summary_line() -> str:
    """One concise line for the system prompt (cheap — reads cached info)."""
    try:
        info = get_deployment_info()
    except Exception:
        return "unknown"
    ts = info["tailscale"]
    if info["public_base_url"]:
        head = f"publicly reachable at {info['public_base_url']} (via {info['public_via']})"
    elif ts.get("available"):
        head = f"on a tailnet as {ts.get('hostname')} (no public serve mapping detected)"
    elif info["ts_hostname_env"]:
        head = f"behind a Tailscale sidecar (hostname '{info['ts_hostname_env']}')"
    else:
        head = "local only — off-network users (e.g. Discord) cannot reach localhost/private URLs"
    eff = info.get("effective_base_url")
    if eff and str(info.get("effective_via", "")).startswith("auto:"):
        tail = f"; links use {eff} (auto-detected)"
    elif eff:
        tail = f"; links use {eff}"
    else:
        tail = ""
    note = "  ⚠ " + info["advisories"][0] if info["advisories"] else ""
    return head + tail + note


def format_report() -> str:
    """Fuller human-readable report for the deployment_info tool."""
    info = get_deployment_info()
    ts = info["tailscale"]
    lines = ["**Deployment / reachability**",
             f"- Runtime: {'Docker' if info['in_docker'] else 'local host'}"]
    if ts.get("available"):
        lines.append(f"- Tailscale: ACTIVE — hostname `{ts.get('hostname')}`, "
                     f"IPs {', '.join(ts.get('ips') or []) or '-'}")
    elif info["ts_hostname_env"]:
        lines.append(f"- Tailscale: sidecar configured (TS_HOSTNAME=`{info['ts_hostname_env']}`); "
                     "this process can't query the sidecar's tailnet directly")
    else:
        lines.append(f"- Tailscale: not detected ({ts.get('reason', 'unknown')})")
    if info["ngrok_url"]:
        lines.append(f"- ngrok: {info['ngrok_url']}")
    if info["serve_mappings"]:
        lines.append("- tailscale serve:")
        lines += [f"    {m}" for m in info["serve_mappings"]]
    lines.append(f"- Public base URL (best guess): {info['public_base_url'] or '(none — local only)'}")
    lines.append(f"- TEAMWORK_BASE_URL (configured): {info['teamwork_base_url'] or '(unset)'}")
    lines.append(f"- Effective base URL (links): {info.get('effective_base_url') or '(none)'} "
                 f"[{info.get('effective_via') or 'n/a'}]")
    if info["advisories"]:
        lines.append("- Advisories:")
        lines += [f"    ⚠ {a}" for a in info["advisories"]]
    return "\n".join(lines)
