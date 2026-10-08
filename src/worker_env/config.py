"""Environment-driven settings for the worker-env API and CLI."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from worker_env.signing import KEY_LENGTH_BYTES, b64_decode

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_PORT = 65535
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

StoreName = Literal["memory", "vault"]


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration resolved from environment variables.

    Attributes:
        store: Backend used to persist contexts.
        vault_url: Base URL of the Cloudflare vault Worker.
        master_private_key: Raw Ed25519 private key used to sign outbound vault calls.
        master_public_keys: Raw Ed25519 public keys trusted to call this API.
        allow_insecure: Development-only flag that disables request signing.
        host: Loopback interface the server binds to.
        port: TCP port the server binds to.
    """

    store: StoreName
    vault_url: str | None
    master_private_key: bytes | None
    master_public_keys: tuple[bytes, ...]
    allow_insecure: bool
    host: str
    port: int


def _resolve_store(raw: str, vault_url: str | None) -> StoreName:
    """Resolve the store backend name from raw configuration text.

    Args:
        raw: Value of ``WORKER_ENV_STORE``; empty selects a default.
        vault_url: Configured vault URL, used to pick a sensible default.

    Returns:
        The resolved store name.

    Raises:
        ValueError: If the value is not ``memory`` or ``vault``.
    """
    value = raw.strip().lower()
    if value == "":
        return "vault" if vault_url is not None else "memory"
    if value in {"memory", "vault"}:
        return cast("StoreName", value)
    raise ValueError(f"WORKER_ENV_STORE must be 'memory' or 'vault', got {raw!r}")


def _load_private_key(raw: str | None) -> bytes | None:
    """Decode the optional outbound-signing private key.

    Args:
        raw: Base64 value of ``WORKER_ENV_MASTER_PRIVATE_KEY``.

    Returns:
        The raw 32-byte key, or ``None`` when unset.

    Raises:
        ValueError: If the value is not base64 or not 32 bytes.
    """
    if raw is None or not raw.strip():
        return None
    try:
        key = b64_decode(raw.strip())
    except ValueError as exc:
        raise ValueError("WORKER_ENV_MASTER_PRIVATE_KEY must be base64") from exc
    if len(key) != KEY_LENGTH_BYTES:
        raise ValueError(f"WORKER_ENV_MASTER_PRIVATE_KEY must decode to {KEY_LENGTH_BYTES} bytes")
    return key


def _load_public_keys(raw: str | None) -> tuple[bytes, ...]:
    """Decode the comma-separated trusted public key list.

    Args:
        raw: Value of ``WORKER_ENV_MASTER_PUBLIC_KEYS``.

    Returns:
        The decoded trusted public keys.

    Raises:
        ValueError: If any entry is not base64 or not 32 bytes.
    """
    if raw is None:
        return ()
    keys: list[bytes] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            key = b64_decode(text)
        except ValueError as exc:
            raise ValueError(
                "WORKER_ENV_MASTER_PUBLIC_KEYS must be a comma-separated base64 list"
            ) from exc
        if len(key) != KEY_LENGTH_BYTES:
            raise ValueError(
                f"each WORKER_ENV_MASTER_PUBLIC_KEYS entry must decode to {KEY_LENGTH_BYTES} bytes"
            )
        keys.append(key)
    return tuple(keys)


def _parse_port(raw: str) -> int:
    """Parse and range-check the server port.

    Args:
        raw: Value of ``WORKER_ENV_PORT``.

    Returns:
        The validated port number.

    Raises:
        ValueError: If the value is not an integer in ``1..65535``.
    """
    try:
        port = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"WORKER_ENV_PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= MAX_PORT:
        raise ValueError(f"WORKER_ENV_PORT out of range: {port}")
    return port


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load and validate settings from a mapping of environment variables.

    Recognised variables:

    - ``WORKER_ENV_STORE``: ``memory`` or ``vault`` (default: ``vault`` when
      a vault URL is configured, otherwise ``memory``).
    - ``WORKER_ENV_VAULT_URL``: base URL of the Cloudflare vault Worker.
    - ``WORKER_ENV_MASTER_PRIVATE_KEY``: base64 raw Ed25519 private key.
    - ``WORKER_ENV_MASTER_PUBLIC_KEYS``: comma-separated base64 raw Ed25519
      public keys trusted to call the API.
    - ``WORKER_ENV_ALLOW_INSECURE``: ``true`` disables signing (dev only).
    - ``WORKER_ENV_HOST``: loopback bind address (default ``127.0.0.1``).
    - ``WORKER_ENV_PORT``: bind port (default ``8000``).

    Args:
        env: Mapping of environment variables; defaults to ``os.environ``.

    Returns:
        Validated settings.

    Raises:
        ValueError: If any variable is missing, malformed, or would bind a
            non-loopback address.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    vault_url = source.get("WORKER_ENV_VAULT_URL", "").strip() or None
    store = _resolve_store(source.get("WORKER_ENV_STORE", ""), vault_url)
    if store == "vault" and vault_url is None:
        raise ValueError("WORKER_ENV_STORE=vault requires WORKER_ENV_VAULT_URL")
    private_key = _load_private_key(source.get("WORKER_ENV_MASTER_PRIVATE_KEY"))
    public_keys = _load_public_keys(source.get("WORKER_ENV_MASTER_PUBLIC_KEYS"))
    allow_insecure = source.get("WORKER_ENV_ALLOW_INSECURE", "").strip().lower() in TRUE_VALUES
    host = source.get("WORKER_ENV_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"WORKER_ENV_HOST must be a loopback address, got {host!r}; 0.0.0.0 is not permitted"
        )
    port = _parse_port(source.get("WORKER_ENV_PORT", str(DEFAULT_PORT)))
    return Settings(
        store=store,
        vault_url=vault_url,
        master_private_key=private_key,
        master_public_keys=public_keys,
        allow_insecure=allow_insecure,
        host=host,
        port=port,
    )
