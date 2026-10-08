"""Shared fixtures and a signing test client for worker-env tests."""

import json as jsonlib
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_env.api import create_app
from worker_env.config import Settings
from worker_env.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    KeyPair,
    current_timestamp,
    generate_keypair,
    sign_request,
)
from worker_env.store import InMemoryContextStore

TEST_KEY_PAIR: KeyPair = generate_keypair()
SAMPLE_CONTEXT: dict[str, str] = {
    "namespace": "app",
    "name": "db-url",
    "label": "Primary database URL",
    "value": "postgres://localhost:5432/app",
}


def build_settings(
    *,
    allow_insecure: bool = False,
    public_keys: tuple[bytes, ...] | None = None,
    private_key: bytes | None = None,
) -> Settings:
    """Build deterministic in-memory settings for tests.

    Args:
        allow_insecure: Whether to disable request signing.
        public_keys: Trusted keys; defaults to the shared test public key.
        private_key: Outbound signing key; defaults to the shared test key.

    Returns:
        Settings bound to the memory store on loopback.
    """
    return Settings(
        store="memory",
        vault_url=None,
        master_private_key=TEST_KEY_PAIR.private_key if private_key is None else private_key,
        master_public_keys=((TEST_KEY_PAIR.public_key,) if public_keys is None else public_keys),
        allow_insecure=allow_insecure,
        host="127.0.0.1",
        port=8000,
    )


class SignedClient:
    """TestClient wrapper that signs every request with the test key pair."""

    def __init__(self, client: TestClient, private_key: bytes) -> None:
        """Wrap a test client.

        Args:
            client: Underlying unsigned client.
            private_key: Raw Ed25519 private key used for signing.
        """
        self._client = client
        self._private_key = private_key

    @property
    def raw(self) -> TestClient:
        """Return the underlying client for unsigned requests.

        Returns:
            The wrapped :class:`TestClient`.
        """
        return self._client

    def get(
        self,
        url: str,
        *,
        sign: bool = True,
        timestamp: int | None = None,
        signature: str | None = None,
    ) -> httpx.Response:
        """Send a signed GET request.

        Args:
            url: Request path including any query string.
            sign: Whether to attach signature headers.
            timestamp: Explicit timestamp override in Unix seconds.
            signature: Explicit signature override.

        Returns:
            The decoded HTTP response.
        """
        return self._send("GET", url, b"", sign=sign, timestamp=timestamp, signature=signature)

    def post(
        self,
        url: str,
        json: Any,
        *,
        sign: bool = True,
        timestamp: int | None = None,
        signature: str | None = None,
    ) -> httpx.Response:
        """Send a signed POST request with a JSON body.

        Args:
            url: Request path.
            json: JSON-serialisable request body.
            sign: Whether to attach signature headers.
            timestamp: Explicit timestamp override in Unix seconds.
            signature: Explicit signature override.

        Returns:
            The decoded HTTP response.
        """
        body = jsonlib.dumps(json, separators=(",", ":")).encode("utf-8")
        return self._send("POST", url, body, sign=sign, timestamp=timestamp, signature=signature)

    def patch(
        self,
        url: str,
        json: Any,
        *,
        sign: bool = True,
        timestamp: int | None = None,
        signature: str | None = None,
    ) -> httpx.Response:
        """Send a signed PATCH request with a JSON body.

        Args:
            url: Request path.
            json: JSON-serialisable request body.
            sign: Whether to attach signature headers.
            timestamp: Explicit timestamp override in Unix seconds.
            signature: Explicit signature override.

        Returns:
            The decoded HTTP response.
        """
        body = jsonlib.dumps(json, separators=(",", ":")).encode("utf-8")
        return self._send("PATCH", url, body, sign=sign, timestamp=timestamp, signature=signature)

    def delete(
        self,
        url: str,
        *,
        sign: bool = True,
        timestamp: int | None = None,
        signature: str | None = None,
    ) -> httpx.Response:
        """Send a signed DELETE request.

        Args:
            url: Request path.
            sign: Whether to attach signature headers.
            timestamp: Explicit timestamp override in Unix seconds.
            signature: Explicit signature override.

        Returns:
            The decoded HTTP response.
        """
        return self._send("DELETE", url, b"", sign=sign, timestamp=timestamp, signature=signature)

    def _send(
        self,
        method: str,
        url: str,
        body: bytes,
        *,
        sign: bool,
        timestamp: int | None,
        signature: str | None,
    ) -> httpx.Response:
        """Send one request with optional signature headers.

        Args:
            method: HTTP method.
            url: Request path including any query string.
            body: Raw request body bytes.
            sign: Whether to attach signature headers.
            timestamp: Explicit timestamp override in Unix seconds.
            signature: Explicit signature override.

        Returns:
            The decoded HTTP response.
        """
        headers: dict[str, str] = {}
        if body:
            headers["Content-Type"] = "application/json"
        if sign:
            signed_at = current_timestamp() if timestamp is None else timestamp
            headers[TIMESTAMP_HEADER] = str(signed_at)
            headers[SIGNATURE_HEADER] = (
                signature
                if signature is not None
                else sign_request(self._private_key, signed_at, method, url, body)
            )
        content = body or None
        return self._client.request(method, url, content=content, headers=headers)


@pytest.fixture
def settings() -> Settings:
    """Return deterministic settings for the in-memory backend.

    Returns:
        Settings trusting the shared test key pair.
    """
    return build_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Build an application backed by a fresh in-memory store.

    Args:
        settings: Deterministic test settings.

    Returns:
        A configured application instance.
    """
    return create_app(settings=settings, store=InMemoryContextStore())


@pytest.fixture
def client(app: FastAPI) -> SignedClient:
    """Return a signing client bound to the test application.

    Args:
        app: Application under test.

    Returns:
        A client that signs every request with the shared test key.
    """
    return SignedClient(TestClient(app), TEST_KEY_PAIR.private_key)
