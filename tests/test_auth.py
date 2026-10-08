"""API tests for Ed25519 request authentication."""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import SAMPLE_CONTEXT, SignedClient, build_settings
from worker_env.api import create_app
from worker_env.config import Settings
from worker_env.signing import (
    MAX_CLOCK_SKEW_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    generate_keypair,
    sign_request,
)


def test_unsigned_request_is_rejected(client: SignedClient) -> None:
    """Requests without signature headers receive a 401."""
    response = client.raw.get("/contexts")
    assert response.status_code == 401
    assert "signature" in response.json()["detail"]


def test_garbage_signature_is_rejected(client: SignedClient) -> None:
    """A syntactically invalid signature is rejected."""
    response = client.get("/contexts", signature="not-a-signature")
    assert response.status_code == 401


def test_signature_from_untrusted_key_is_rejected(client: SignedClient) -> None:
    """A well-formed signature from a different key pair is rejected."""
    stranger = generate_keypair()
    timestamp = int(time.time())
    forged = sign_request(
        stranger.private_key,
        timestamp,
        "GET",
        "/contexts",
        b"",
    )
    response = client.get(
        "/contexts",
        timestamp=timestamp,
        signature=forged,
    )
    assert response.status_code == 401


def test_stale_timestamp_is_rejected(client: SignedClient) -> None:
    """Signatures older than the skew window are rejected."""
    stale = int(time.time()) - MAX_CLOCK_SKEW_SECONDS - 30
    response = client.get("/contexts", timestamp=stale)
    assert response.status_code == 401


def test_future_timestamp_is_rejected(client: SignedClient) -> None:
    """Timestamps far ahead of the server clock are rejected."""
    future = int(time.time()) + MAX_CLOCK_SKEW_SECONDS + 30
    response = client.get("/contexts", timestamp=future)
    assert response.status_code == 401


def test_tampered_body_is_rejected(client: SignedClient) -> None:
    """Signing one body and sending another fails verification."""
    legitimate = dict(SAMPLE_CONTEXT)
    signed_over = b'{"tampered":true}'
    timestamp = int(time.time())
    signature = sign_request(
        build_settings().master_private_key or b"",
        timestamp,
        "POST",
        "/contexts",
        signed_over,
    )
    response = client.raw.post(
        "/contexts",
        json=legitimate,
        headers={
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_path_is_bound_to_signature(client: SignedClient) -> None:
    """A signature over one path cannot authenticate a different path."""
    timestamp = int(time.time())
    signature = sign_request(
        build_settings().master_private_key or b"",
        timestamp,
        "GET",
        "/contexts?namespace=app",
        b"",
    )
    response = client.get(
        "/contexts",
        timestamp=timestamp,
        signature=signature,
    )
    assert response.status_code == 401


def test_valid_signature_is_accepted(client: SignedClient) -> None:
    """A fresh, correctly signed request succeeds."""
    assert client.get("/contexts").status_code == 200


def test_health_needs_no_signature(app: FastAPI) -> None:
    """The container health probe works without credentials."""
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_app_requires_trusted_keys() -> None:
    """Building the app without public keys fails closed."""
    untrusted = Settings(
        store="memory",
        vault_url=None,
        master_private_key=None,
        master_public_keys=(),
        allow_insecure=False,
        host="127.0.0.1",
        port=8000,
    )
    with pytest.raises(ValueError, match="WORKER_ENV_MASTER_PUBLIC_KEYS"):
        create_app(settings=untrusted)


def test_allow_insecure_disables_signing() -> None:
    """Insecure mode is an explicit opt-out that serves unsigned traffic."""
    permissive = build_settings(allow_insecure=True, public_keys=())
    app = create_app(settings=permissive)
    response = TestClient(app).get("/contexts")
    assert response.status_code == 200
