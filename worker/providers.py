"""LLM provider catalogue for the single gateway.

Mirrors the catalogue previously shipped inside the yuktiAI app: a
"provider profile" is a named OpenAI-compatible endpoint. The gateway holds one
API key per profile in the vault and applies it server-side, so the browser
never sees a key.

A profile can exist in three ways:

1. Built-in  — listed in ``BUILTIN_PROFILES`` below.
2. Env-defined — added at deploy time via the ``EXTRA_PROVIDERS`` var (JSON
   array of profiles). An entry whose ``id`` matches a built-in overrides that
   built-in, handy for pinning a different default model or host.
3. Keyed  — a provider only becomes available when vault context ``yuktiai``
   (or the default context) defines its key.

Secret naming (see :func:`secret_name_for`): profile id "groq" -> vault key
``KEY_GROQ``; id "my-vllm" -> ``KEY_MY_VLLM``.

The base URL of any profile can be overridden per deployment with
``BASE_URL_<SLUG>``, which is how a profile is pointed at a self-hosted or
regional endpoint without touching this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderProfile:
    """One named OpenAI-compatible endpoint the gateway can proxy to."""

    id: str
    label: str
    base_url: str
    default_model: str = ""
    models: list[str] = field(default_factory=list)


BUILTIN_PROFILES: list[ProviderProfile] = [
    ProviderProfile(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="groq/compound-mini",
        models=["groq/compound-mini"],
    ),
    ProviderProfile(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        models=["gpt-4o-mini", "gpt-4o"],
    ),
    ProviderProfile(
        id="anthropic",
        label="Anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-haiku-4-5-20251001",
        models=["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5"],
    ),
    ProviderProfile(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="meta-llama/llama-3.3-70b-instruct",
    ),
    ProviderProfile(
        id="together",
        label="Together AI",
        base_url="https://api.together.xyz/v1",
        default_model="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    ),
    ProviderProfile(
        id="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
    ),
    ProviderProfile(
        id="google",
        label="Google",
        base_url="https://generativelanguage.googleapis.com/v1beta/interactions",
        default_model="gemini-2.5-flash",
        models=["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.8-flash"],
    ),
]


def slug_for(provider_id: str) -> str:
    """Upper-case the id, collapsing every run of non-alphanumerics to one underscore.

    Args:
        provider_id: Profile id, e.g. "my-llama.2".

    Returns:
        Upper-cased slug, e.g. "MY_LLAMA_2".
    """
    out: list[str] = []
    for char in provider_id.upper():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out)


def secret_name_for(provider_id: str) -> str:
    """Return the vault key that holds a profile's API key.

    Args:
        provider_id: Profile id.

    Returns:
        Vault secret name, e.g. "KEY_GROQ".
    """
    return f"KEY_{slug_for(provider_id)}"


def base_url_var_for(provider_id: str) -> str:
    """Return the env var name that overrides a profile's base URL.

    Args:
        provider_id: Profile id.

    Returns:
        Environment variable name, e.g. "BASE_URL_GROQ".
    """
    return f"BASE_URL_{slug_for(provider_id)}"


def _parse_extra_profiles(raw: str | None) -> list[ProviderProfile]:
    """Parse the ``EXTRA_PROVIDERS`` var; malformed entries are dropped, not fatal."""
    if not raw or not raw.strip():
        return []
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        print("worker-env: EXTRA_PROVIDERS is not valid JSON — ignored")
        return []
    if not isinstance(parsed, list):
        print("worker-env: EXTRA_PROVIDERS must be a JSON array — ignored")
        return []

    profiles: list[ProviderProfile] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        profile_id = entry.get("id")
        base_url = entry.get("base_url") or entry.get("baseUrl")
        if not isinstance(profile_id, str) or not isinstance(base_url, str):
            continue
        models = entry.get("models") or entry.get("models")
        profiles.append(
            ProviderProfile(
                id=profile_id,
                label=str(entry.get("label", profile_id)),
                base_url=base_url.rstrip("/"),
                default_model=str(entry.get("defaultModel", entry.get("default_model", ""))),
                models=list(models) if isinstance(models, list) else [],
            )
        )
    return profiles


def resolve_profiles(extra_raw: str | None) -> list[ProviderProfile]:
    """Merge built-ins with ``EXTRA_PROVIDERS``; later entries win on id collision.

    Args:
        extra_raw: Raw ``EXTRA_PROVIDERS`` var, or None.

    Returns:
        Ordered provider catalogue with duplicates resolved.
    """
    by_id: dict[str, ProviderProfile] = {p.id: p for p in BUILTIN_PROFILES}
    for extra in _parse_extra_profiles(extra_raw):
        existing = by_id.get(extra.id)
        if existing is not None:
            by_id[extra.id] = ProviderProfile(
                id=extra.id,
                label=extra.label,
                base_url=extra.base_url,
                default_model=extra.default_model or existing.default_model,
                models=extra.models or existing.models,
            )
        else:
            by_id[extra.id] = extra
    return list(by_id.values())
