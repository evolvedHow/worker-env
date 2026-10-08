"""End-to-end tests for the vault-backed store against a live API instance."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import SAMPLE_CONTEXT, SignedClient, build_settings
from worker_env.api import create_app
from worker_env.client import VaultClient
from worker_env.config import Settings
from worker_env.store import InMemoryContextStore, VaultContextStore


def build_vault_app(settings: Settings) -> FastAPI:
    """Stand in for the Cloudflare vault using the same API contract.

    Args:
        settings: Settings trusted by the simulated vault.

    Returns:
        An application exposing the vault's HTTP surface.
    """
    return create_app(settings=settings, store=InMemoryContextStore())


def build_vault_backed_app(settings: Settings) -> FastAPI:
    """Build an API whose store delegates every call to the simulated vault.

    Args:
        settings: Settings shared by both layers.

    Returns:
        An application backed by :class:`VaultContextStore`.
    """
    vault_app = build_vault_app(settings)
    client = VaultClient(
        "http://vault.test",
        settings.master_private_key or b"",
        transport=httpx.ASGITransport(app=vault_app),
    )
    return create_app(settings=settings, store=VaultContextStore(client))


def test_vault_backed_crud_roundtrip(settings: Settings) -> None:
    """Create, read, list, update, and delete flow through the vault."""
    app = build_vault_backed_app(settings)
    with TestClient(app) as raw:
        api = SignedClient(raw, settings.master_private_key or b"")

        created = api.post("/contexts", json=SAMPLE_CONTEXT)
        assert created.status_code == 201
        assert created.json()["usage_count"] == 0

        duplicate = api.post(
            "/contexts",
            json={**SAMPLE_CONTEXT, "name": "other"},
        )
        assert duplicate.status_code == 409

        first = api.get("/contexts/app/db-url")
        assert first.status_code == 200
        assert first.json()["usage_count"] == 1
        second = api.get("/contexts/app/db-url")
        assert second.json()["usage_count"] == 2

        listed = api.get("/contexts")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["contexts"][0]["usage_count"] == 2

        patched = api.patch(
            "/contexts/app/db-url",
            json={"label": "rotated", "value": "postgres://new/app"},
        )
        assert patched.status_code == 200
        assert patched.json()["label"] == "rotated"
        assert patched.json()["value"] == "postgres://new/app"
        assert patched.json()["usage_count"] == 2
        assert patched.json()["create_date"] == created.json()["create_date"]

        assert api.delete("/contexts/app/db-url").status_code == 204
        assert api.get("/contexts/app/db-url").status_code == 404


def test_vault_backed_uniqueness_and_missing_keys(settings: Settings) -> None:
    """Uniqueness conflicts and missing keys surface as 409 and 404."""
    app = build_vault_backed_app(settings)
    with TestClient(app) as raw:
        api = SignedClient(raw, settings.master_private_key or b"")

        first = {"namespace": "app", "name": "one", "label": "a", "value": "shared"}
        second = {"namespace": "app", "name": "two", "label": "b", "value": "unique"}
        assert api.post("/contexts", json=first).status_code == 201
        assert api.post("/contexts", json=second).status_code == 201

        conflict = api.post(
            "/contexts",
            json={"namespace": "app", "name": "three", "label": "c", "value": "shared"},
        )
        assert conflict.status_code == 409

        value_conflict = api.patch("/contexts/app/two", json={"value": "shared"})
        assert value_conflict.status_code == 409

        assert api.get("/contexts/app/missing").status_code == 404
        assert api.patch("/contexts/app/missing", json={"label": "x"}).status_code == 404
        assert api.delete("/contexts/app/missing").status_code == 404


def test_vault_backed_namespace_filter(settings: Settings) -> None:
    """Namespace filtering is preserved across the vault boundary."""
    app = build_vault_backed_app(settings)
    with TestClient(app) as raw:
        api = SignedClient(raw, settings.master_private_key or b"")
        assert api.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
        other = {**SAMPLE_CONTEXT, "namespace": "staging"}
        assert api.post("/contexts", json=other).status_code == 201

        scoped = api.get("/contexts?namespace=staging")
        assert scoped.status_code == 200
        assert scoped.json()["count"] == 1
        assert scoped.json()["contexts"][0]["namespace"] == "staging"


def test_vault_store_requires_url_and_key() -> None:
    """Building the vault store without URL or signing key fails clearly."""
    base = build_settings()
    missing_url = Settings(
        store="vault",
        vault_url=None,
        master_private_key=base.master_private_key,
        master_public_keys=base.master_public_keys,
        allow_insecure=False,
        host="127.0.0.1",
        port=8000,
    )
    with pytest.raises(ValueError, match="WORKER_ENV_VAULT_URL"):
        create_app(settings=missing_url)

    missing_key = Settings(
        store="vault",
        vault_url="https://vault.example",
        master_private_key=None,
        master_public_keys=base.master_public_keys,
        allow_insecure=False,
        host="127.0.0.1",
        port=8000,
    )
    with pytest.raises(ValueError, match="WORKER_ENV_MASTER_PRIVATE_KEY"):
        create_app(settings=missing_key)
