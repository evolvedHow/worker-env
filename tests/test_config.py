"""Tests for environment-driven configuration parsing."""

import pytest

from worker_env.config import MAX_PORT, load_settings
from worker_env.signing import b64_encode, generate_keypair


def test_defaults_pick_memory_store() -> None:
    """Without a vault URL the memory store is selected."""
    settings = load_settings(env={})
    assert settings.store == "memory"
    assert settings.vault_url is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.master_public_keys == ()
    assert settings.allow_insecure is False


def test_vault_url_selects_vault_store() -> None:
    """Configuring a vault URL switches the backend automatically."""
    settings = load_settings(env={"WORKER_ENV_VAULT_URL": "https://vault.example"})
    assert settings.store == "vault"
    assert settings.vault_url == "https://vault.example"


def test_explicit_memory_store_wins() -> None:
    """An explicit store selection overrides the default heuristic."""
    settings = load_settings(
        env={
            "WORKER_ENV_VAULT_URL": "https://vault.example",
            "WORKER_ENV_STORE": "memory",
        }
    )
    assert settings.store == "memory"


def test_vault_store_requires_url() -> None:
    """Selecting the vault store without a URL is an error."""
    with pytest.raises(ValueError, match="WORKER_ENV_VAULT_URL"):
        load_settings(env={"WORKER_ENV_STORE": "vault"})


def test_unknown_store_is_rejected() -> None:
    """Unrecognised store names fail with a clear message."""
    with pytest.raises(ValueError, match="WORKER_ENV_STORE"):
        load_settings(env={"WORKER_ENV_STORE": "redis"})


def test_non_loopback_host_is_rejected() -> None:
    """Binding any non-loopback address is refused."""
    with pytest.raises(ValueError, match="loopback"):
        load_settings(env={"WORKER_ENV_HOST": "0.0.0.0"})


def test_port_validation() -> None:
    """Ports must be integers inside the valid range."""
    with pytest.raises(ValueError, match="WORKER_ENV_PORT"):
        load_settings(env={"WORKER_ENV_PORT": "eight-thousand"})
    with pytest.raises(ValueError, match="out of range"):
        load_settings(env={"WORKER_ENV_PORT": str(MAX_PORT + 1)})


def test_key_parsing_roundtrip() -> None:
    """Public and private keys decode from base64 configuration."""
    pair = generate_keypair()
    settings = load_settings(
        env={
            "WORKER_ENV_MASTER_PRIVATE_KEY": b64_encode(pair.private_key),
            "WORKER_ENV_MASTER_PUBLIC_KEYS": b64_encode(pair.public_key),
        }
    )
    assert settings.master_private_key == pair.private_key
    assert settings.master_public_keys == (pair.public_key,)


def test_public_key_list_supports_multiple_entries() -> None:
    """Trusted keys accept a comma-separated list."""
    first = generate_keypair()
    second = generate_keypair()
    settings = load_settings(
        env={
            "WORKER_ENV_MASTER_PUBLIC_KEYS": (
                f"{b64_encode(first.public_key)}, {b64_encode(second.public_key)}"
            )
        }
    )
    assert settings.master_public_keys == (first.public_key, second.public_key)


def test_malformed_keys_are_rejected() -> None:
    """Invalid base64 and wrong key lengths fail with clear errors."""
    with pytest.raises(ValueError, match="base64"):
        load_settings(env={"WORKER_ENV_MASTER_PRIVATE_KEY": "!!!"})
    with pytest.raises(ValueError, match="32 bytes"):
        load_settings(env={"WORKER_ENV_MASTER_PRIVATE_KEY": b64_encode(b"short")})
    with pytest.raises(ValueError, match="32 bytes"):
        load_settings(env={"WORKER_ENV_MASTER_PUBLIC_KEYS": b64_encode(b"short")})


def test_allow_insecure_flag_parsing() -> None:
    """The insecure flag only activates for explicit truthy values."""
    assert load_settings(env={"WORKER_ENV_ALLOW_INSECURE": "true"}).allow_insecure
    assert load_settings(env={"WORKER_ENV_ALLOW_INSECURE": "1"}).allow_insecure
    assert not load_settings(env={"WORKER_ENV_ALLOW_INSECURE": "no"}).allow_insecure
