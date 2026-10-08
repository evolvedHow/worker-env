"""HTTP client that signs every request before calling the vault."""

import json
from typing import Any, cast

import httpx

from worker_env.errors import VaultError
from worker_env.models import Context, ContextCreate, ContextUpdate
from worker_env.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    current_timestamp,
    sign_request,
)

CLIENT_ERROR_STATUS = 400


class VaultClient:
    """Ed25519-signing HTTP client for the Cloudflare vault Worker.

    A single client owns one pooled :class:`httpx.AsyncClient`, so
    connections are reused across calls and every request carries a fresh
    signature over its exact method, path, and body bytes.
    """

    def __init__(
        self,
        base_url: str,
        private_key: bytes,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Create a signing client for a vault URL.

        Args:
            base_url: Base URL of the deployed vault Worker.
            private_key: Raw 32-byte Ed25519 private key used to sign requests.
            transport: Optional transport override, used by tests.
            timeout: Per-request timeout in seconds.
        """
        self._private_key = private_key
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: ContextCreate | ContextUpdate | None = None,
    ) -> dict[str, Any]:
        """Send one signed request and return the decoded JSON object.

        Args:
            method: HTTP method to send.
            path: Request path including any query string.
            payload: Optional Pydantic model serialised as the JSON body.

        Returns:
            The decoded JSON response body as a mapping.

        Raises:
            VaultError: If the vault answers with an HTTP error status.
        """
        if payload is None:
            body = b""
        else:
            body = json.dumps(
                payload.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        timestamp = current_timestamp()
        headers = {
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign_request(
                self._private_key,
                timestamp,
                method,
                path,
                body,
            ),
        }
        if body:
            headers["Content-Type"] = "application/json"
        response = await self._http.request(
            method,
            path,
            content=body,
            headers=headers,
        )
        if response.status_code >= CLIENT_ERROR_STATUS:
            raise VaultError(response.status_code, self._error_detail(response))
        if not response.content:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            raise VaultError(response.status_code, "vault returned a non-object JSON body")
        return data

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract a human-readable detail string from an error response.

        Args:
            response: Error response returned by the vault.

        Returns:
            The vault's ``detail`` field, or a generic status description.
        """
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail:
                return detail
        return f"vault returned status {response.status_code}"

    async def create(self, payload: ContextCreate) -> Context:
        """Create a context in the vault.

        Args:
            payload: Validated creation payload.

        Returns:
            The stored context record.
        """
        data = await self._request("POST", "/contexts", payload=payload)
        return Context.model_validate(data)

    async def get(self, namespace: str, name: str) -> Context:
        """Fetch a single context, incrementing its usage counter.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Returns:
            The stored context record with the updated usage counter.
        """
        data = await self._request("GET", f"/contexts/{namespace}/{name}")
        return Context.model_validate(data)

    async def list_contexts(self, namespace: str | None = None) -> list[Context]:
        """List contexts without touching usage counters.

        Args:
            namespace: Optional namespace filter.

        Returns:
            The matching context records.
        """
        path = "/contexts" if namespace is None else f"/contexts?namespace={namespace}"
        data = await self._request("GET", path)
        items = cast("list[Any]", data.get("contexts", []))
        return [Context.model_validate(item) for item in items]

    async def update(self, namespace: str, name: str, patch: ContextUpdate) -> Context:
        """Update the mutable fields of a context.

        Args:
            namespace: Namespace owning the context.
            name: Context name.
            patch: Payload containing ``label`` and/or ``value``.

        Returns:
            The updated context record.
        """
        data = await self._request("PATCH", f"/contexts/{namespace}/{name}", payload=patch)
        return Context.model_validate(data)

    async def delete(self, namespace: str, name: str) -> None:
        """Delete a context from the vault.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Raises:
            VaultError: If the vault answers with an HTTP error status.
        """
        await self._request("DELETE", f"/contexts/{namespace}/{name}")
