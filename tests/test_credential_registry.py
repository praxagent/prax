"""Drift guard: the credential registry must stay in lock-step with settings.py.

This is the mechanism behind "Prax and the proxy never drift": if someone adds a
new API-key field to settings.py without classifying it in
prax/services/credential_registry.py, this test FAILS — so a secret can never
silently be added that the proxy story doesn't account for.
"""
from __future__ import annotations

from prax.services import credential_registry as reg
from prax.settings import AppSettings

# Substrings that mark a settings alias as a probable secret credential. The
# reverse check (settings → registry) uses these to catch a forgotten new key.
_SECRET_MARKERS = ("_KEY", "_TOKEN", "_SECRET", "_SID", "_PASSWORD", "AUTHKEY")


def _is_str_field(field_info) -> bool:
    """True if the field can hold a string secret (str or str | None)."""
    ann = field_info.annotation
    if ann is str:
        return True
    from typing import get_args
    return str in get_args(ann)


def _settings_credential_aliases() -> set[str]:
    out: set[str] = set()
    for name, fi in AppSettings.model_fields.items():
        alias = fi.alias or name.upper()
        if alias in reg.NON_CREDENTIAL_ALIASES:
            continue
        if not _is_str_field(fi):
            continue  # bool flags / ints can't be secrets
        if any(m in alias for m in _SECRET_MARKERS) or alias.endswith("_API"):
            out.add(alias)
    return out


def test_every_settings_credential_is_registered():
    """A new *_KEY/_TOKEN/_SECRET/_API field in settings.py MUST be classified in
    the registry. If this fails: add a Credential(...) row (see the module docstring)."""
    settings_creds = _settings_credential_aliases()
    registered = reg.all_envs()
    missing = settings_creds - registered
    assert not missing, (
        f"These credential env vars are in settings.py but NOT in the credential "
        f"registry — classify them (PROXY_MODEL/FORWARD/LOCAL) so the proxy story "
        f"can't drift: {sorted(missing)}"
    )


def test_registry_has_no_stale_entries():
    """Every registered env must be a real settings.py alias (no ghosts)."""
    valid_aliases = {
        (fi.alias or name.upper()) for name, fi in AppSettings.model_fields.items()
    }
    stale = reg.all_envs() - valid_aliases
    assert not stale, f"Registry entries with no matching settings.py field: {sorted(stale)}"


def test_no_duplicate_envs():
    envs = [c.env for c in reg.REGISTRY]
    dupes = {e for e in envs if envs.count(e) > 1}
    assert not dupes, f"Duplicate registry envs: {sorted(dupes)}"


def test_proxy_classes_are_valid():
    valid = {reg.PROXY_MODEL, reg.PROXY_FORWARD, reg.PROXY_LOCAL}
    bad = [(c.env, c.proxy) for c in reg.REGISTRY if c.proxy not in valid]
    assert not bad, f"Invalid proxy classification: {bad}"


def test_proxied_credentials_declare_how_to_inject():
    """Anything the proxy actually forwards needs a host + injection method so the
    (Tier-2) forward proxy knows how to route + authenticate it."""
    incomplete = [
        c.env for c in reg.REGISTRY
        if c.proxy in (reg.PROXY_MODEL, reg.PROXY_FORWARD)
        and (not c.inject or (c.proxy == reg.PROXY_MODEL and not c.host))
    ]
    assert not incomplete, f"Proxyable credentials missing host/inject: {incomplete}"


def test_the_two_shipped_model_providers_are_present():
    """OPENAI_KEY + ANTHROPIC_KEY are the Tier-1 providers the proxy handles today."""
    model_envs = {c.env for c in reg.model_credentials()}
    assert {"OPENAI_KEY", "ANTHROPIC_KEY"} <= model_envs
