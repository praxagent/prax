"""GPU power plugin — least-privilege ON/OFF of a cloud GPU.

Prax core stays sleek and holds **no** cloud credential. This plugin only POSTs to
a tiny *user-run* power-broker (reference impl: ``examples/gpu-power-broker/``) that
itself can do nothing but start/stop one pre-provisioned instance. It is
**fail-closed**: unless ``GPU_POWER_BROKER_URL`` is configured it registers **zero**
tools, so there is no GPU-power capability at all.

Everything goes through the capability gateway (`caps`): the HTTP call is
SSRF-guarded (`caps.http_post`/`http_get`), config via `caps.get_config`, and the
bearer token via `caps.get_approved_secret` — no `prax.*` import, no `os.environ`.
See ``docs/guides/cloud-gpu.md`` (design + threat model) and IDEAS_BACKLOG #16.
"""
from __future__ import annotations

from langchain_core.tools import tool

PLUGIN_VERSION = "1"
PLUGIN_DESCRIPTION = (
    "Turn a cloud GPU on/off via a least-privilege power-broker (on/off only — "
    "never create/destroy/resize/SSH/data)."
)

_TIMEOUT = 30
_SECRET = "GPU_POWER_BROKER_TOKEN"


def register(caps):
    broker = (caps.get_config("gpu_power_broker_url") or "").rstrip("/")
    if not broker:
        # Fail-closed: no broker configured → no GPU-power capability whatsoever.
        return []

    def _auth_headers() -> dict:
        try:
            token = caps.get_approved_secret(_SECRET) or ""
        except Exception:
            token = ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _power(action: str) -> str:
        try:
            resp = caps.http_post(
                f"{broker}/power", json={"action": action},
                headers=_auth_headers(), timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            try:
                body = resp.json()
            except Exception:
                body = (resp.text or "").strip()[:200]
            return f"GPU power {action} → {body}"
        except Exception as e:
            return f"GPU power {action} failed: {type(e).__name__}: {e}"

    @tool
    def gpu_power_on() -> str:
        """Power ON the cloud GPU via the configured power-broker.

        Use before running a local model / fine-tune / GPU job; power it OFF when
        done so it stops billing. HIGH-risk (starts a billable instance) — expect
        a confirmation. The broker can ONLY start/stop one instance; nothing else.
        """
        return _power("on")

    @tool
    def gpu_power_off() -> str:
        """Power OFF the cloud GPU via the power-broker to stop billing.

        Call this as soon as the GPU work is finished. HIGH-risk — expect a
        confirmation.
        """
        return _power("off")

    @tool
    def gpu_power_status() -> str:
        """Report whether the cloud GPU is currently on or off (read-only)."""
        try:
            resp = caps.http_get(f"{broker}/power", headers=_auth_headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            try:
                body = resp.json()
            except Exception:
                body = (resp.text or "").strip()[:200]
            return f"GPU power status: {body}"
        except Exception as e:
            return f"GPU power status failed: {type(e).__name__}: {e}"

    return [gpu_power_on, gpu_power_off, gpu_power_status]
