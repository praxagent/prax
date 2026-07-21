"""Secrets proxy — a credential-injecting egress proxy so Prax runs KEYLESS.

Prax runs with NO real LLM API keys in its environment. When it needs to call a
model provider it points its client's base URL at THIS proxy (a separate process
whose env holds the real keys). The proxy strips whatever placeholder auth Prax
sent, injects the real key, forwards to the provider, and streams the response
back. Prax can never read or exfiltrate a key it never holds — the infra-level
"make the secret unreachable" boundary (the real wall; an in-code guard the agent
can edit is only a speed bump — see docs/research/openai-long-horizon-safety.md).

Tier 1 (this module): the two model providers, OpenAI-compatible + Anthropic.
Design + wiring: docs/security/secrets-proxy.md.
"""
from prax.secrets_proxy.app import build_proxy_app
from prax.secrets_proxy.config import ProxyConfig

__all__ = ["ProxyConfig", "build_proxy_app"]
