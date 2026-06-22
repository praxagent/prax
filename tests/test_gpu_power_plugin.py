"""gpu_power plugin — fail-closed by default; least-privilege on/off via the broker."""
from __future__ import annotations

from unittest.mock import MagicMock

from prax.plugins.tools.gpu_power.plugin import register


class _Caps:
    """Minimal PluginCapabilities stub (gateway methods the plugin uses)."""

    def __init__(self, url="", token="tok"):
        self._url, self._token = url, token
        self.http_post = MagicMock()
        self.http_get = MagicMock()

    def get_config(self, key):
        return self._url if key == "gpu_power_broker_url" else None

    def get_approved_secret(self, key):
        return self._token


def _resp(body):
    r = MagicMock()
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


def test_failclosed_when_no_broker():
    # No broker configured → zero tools → no GPU-power capability at all.
    assert register(_Caps(url="")) == []


def test_registers_exactly_three_tools_when_configured():
    tools = register(_Caps(url="https://broker.example/"))
    assert sorted(t.name for t in tools) == [
        "gpu_power_off", "gpu_power_on", "gpu_power_status",
    ]


def test_power_on_posts_action_and_bearer():
    caps = _Caps(url="https://broker.example/", token="sekret")
    caps.http_post.return_value = _resp({"state": "on"})
    tools = {t.name: t for t in register(caps)}
    out = tools["gpu_power_on"].invoke({})
    assert "on" in out
    args, kwargs = caps.http_post.call_args
    assert args[0] == "https://broker.example/power"
    assert kwargs["json"] == {"action": "off"} or kwargs["json"] == {"action": "on"}
    assert kwargs["json"]["action"] == "on"
    assert kwargs["headers"]["Authorization"] == "Bearer sekret"


def test_power_off_posts_off():
    caps = _Caps(url="https://broker.example")
    caps.http_post.return_value = _resp({"state": "off"})
    tools = {t.name: t for t in register(caps)}
    tools["gpu_power_off"].invoke({})
    assert caps.http_post.call_args.kwargs["json"] == {"action": "off"}


def test_status_uses_get():
    caps = _Caps(url="https://broker.example/")
    caps.http_get.return_value = _resp({"state": "off"})
    tools = {t.name: t for t in register(caps)}
    out = tools["gpu_power_status"].invoke({})
    assert "off" in out
    assert caps.http_get.call_args[0][0] == "https://broker.example/power"


def test_risk_classification_high_onoff_low_status():
    from prax.agent.action_policy import RiskLevel, get_risk_level
    assert get_risk_level("gpu_power_on") == RiskLevel.HIGH
    assert get_risk_level("gpu_power_off") == RiskLevel.HIGH
    assert get_risk_level("gpu_power_status") == RiskLevel.LOW
