"""Context store implementations with enforced naming and usage rules."""

import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from worker_env.client import VaultClient
from worker_env.errors import (
    HTTP_CONFLICT,
    HTTP_NOT_FOUND,
    ContextAlreadyExistsError,
    ContextNotFoundError,
    VaultError,
)
from worker_env.models import Context, ContextCreate, ContextUpdate


@runtime_checkable
class ContextStore(Protocol):
    """Persistence backend for context records."""

    async def create(self, payload: ContextCreate) -> Context:
        """Create a context, enforcing unique name and value per namespace."""
        ...

    async def get(self, namespace: str, name: str) -> Context:
        """Fetch a context and increment its usage counter."""
        ...

    async def list_contexts(self, namespace: str | None = None) -> list[Context]:
        """List contexts without incrementing usage counters."""
        ...

    async def update(self, namespace: str, name: str, patch: ContextUpdate) -> Context:
        """Apply a ``label``/``value`` patch to an existing context."""
        ...

    async def delete(self, namespace: str, name: str) -> None:
        """Delete a context and its value index entry."""
        ...

    async def aclose(self) -> None:
        """Release any outbound resources held by the store."""
        ...


class InMemoryContextStore:
    """Dict-backed store used for local development and tests.

    Mutations are guarded by a lock and never span an ``await`` point, so
    each operation is atomic. ``usage_count`` is incremented exclusively by
    :meth:`get`.
    """

    def __init__(self) -> None:
        """Initialise empty name and value indexes."""
        self._contexts: dict[tuple[str, str], Context] = {}
        self._values: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    async def create(self, payload: ContextCreate) -> Context:
        """Create a context, enforcing unique name and value per namespace.

        Args:
            payload: Validated creation payload.

        Returns:
            The stored record with ``usage_count`` set to zero.

        Raises:
            ContextAlreadyExistsError: If the name or value is taken in the namespace.
        """
        with self._lock:
            key = (payload.namespace, payload.name)
            if key in self._contexts:
                raise ContextAlreadyExistsError(
                    f"context '{payload.namespace}/{payload.name}' already exists"
                )
            value_key = (payload.namespace, payload.value)
            if value_key in self._values:
                raise ContextAlreadyExistsError(
                    f"value already used by another context in namespace '{payload.namespace}'"
                )
            record = Context(
                namespace=payload.namespace,
                name=payload.name,
                label=payload.label,
                value=payload.value,
                usage_count=0,
                create_date=datetime.now(UTC),
            )
            self._contexts[key] = record
            self._values[value_key] = payload.name
            return record

    async def get(self, namespace: str, name: str) -> Context:
        """Fetch a context and increment its usage counter.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Returns:
            The stored record after the counter increment.

        Raises:
            ContextNotFoundError: If the context does not exist.
        """
        with self._lock:
            key = (namespace, name)
            record = self._contexts.get(key)
            if record is None:
                raise ContextNotFoundError(namespace, name)
            updated = record.model_copy(update={"usage_count": record.usage_count + 1})
            self._contexts[key] = updated
            return updated

    async def list_contexts(self, namespace: str | None = None) -> list[Context]:
        """List contexts in stable key order without incrementing counters.

        Args:
            namespace: Optional namespace filter.

        Returns:
            The matching records, sorted by namespace then name.
        """
        with self._lock:
            return [
                record
                for (record_namespace, _), record in sorted(self._contexts.items())
                if namespace is None or record_namespace == namespace
            ]

    async def update(self, namespace: str, name: str, patch: ContextUpdate) -> Context:
        """Apply a ``label``/``value`` patch to an existing context.

        Args:
            namespace: Namespace owning the context.
            name: Context name.
            patch: Payload containing ``label`` and/or ``value``.

        Returns:
            The updated record with usage and creation fields untouched.

        Raises:
            ContextNotFoundError: If the context does not exist.
            ContextAlreadyExistsError: If the new value is taken in the namespace.
        """
        with self._lock:
            key = (namespace, name)
            record = self._contexts.get(key)
            if record is None:
                raise ContextNotFoundError(namespace, name)
            updates: dict[str, str] = {}
            if patch.value is not None and patch.value != record.value:
                new_value_key = (namespace, patch.value)
                owner = self._values.get(new_value_key)
                if owner is not None and owner != name:
                    raise ContextAlreadyExistsError(
                        f"value already used by another context in namespace '{namespace}'"
                    )
                self._values.pop((namespace, record.value), None)
                self._values[new_value_key] = name
                updates["value"] = patch.value
            if patch.label is not None:
                updates["label"] = patch.label
            if updates:
                record = record.model_copy(update=updates)
                self._contexts[key] = record
            return record

    async def delete(self, namespace: str, name: str) -> None:
        """Delete a context and its value index entry.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Raises:
            ContextNotFoundError: If the context does not exist.
        """
        with self._lock:
            record = self._contexts.pop((namespace, name), None)
            if record is None:
                raise ContextNotFoundError(namespace, name)
            self._values.pop((namespace, record.value), None)

    async def aclose(self) -> None:
        """Release resources; the in-memory store holds none."""


class VaultContextStore:
    """Store that forwards every operation to the Cloudflare vault Worker.

    The Worker enforces the same uniqueness and usage rules, and HTTP
    error codes are translated back into domain errors so the API layer
    can respond with 404/409 uniformly for both backends.
    """

    def __init__(self, client: VaultClient) -> None:
        """Wrap a signing vault client.

        Args:
            client: Client used for all outbound vault calls.
        """
        self._client = client

    async def create(self, payload: ContextCreate) -> Context:
        """Create a context via the vault.

        Args:
            payload: Validated creation payload.

        Returns:
            The stored context record.

        Raises:
            ContextAlreadyExistsError: If the vault reports a uniqueness conflict.
        """
        try:
            return await self._client.create(payload)
        except VaultError as exc:
            if exc.status_code == HTTP_CONFLICT:
                raise ContextAlreadyExistsError(exc.detail) from exc
            raise

    async def get(self, namespace: str, name: str) -> Context:
        """Fetch a context via the vault, incrementing its usage counter.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Returns:
            The stored context record.

        Raises:
            ContextNotFoundError: If the vault reports the context missing.
        """
        try:
            return await self._client.get(namespace, name)
        except VaultError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise ContextNotFoundError(namespace, name) from exc
            raise

    async def list_contexts(self, namespace: str | None = None) -> list[Context]:
        """List contexts via the vault without incrementing counters.

        Args:
            namespace: Optional namespace filter.

        Returns:
            The matching context records.
        """
        return await self._client.list_contexts(namespace)

    async def update(self, namespace: str, name: str, patch: ContextUpdate) -> Context:
        """Apply a patch via the vault.

        Args:
            namespace: Namespace owning the context.
            name: Context name.
            patch: Payload containing ``label`` and/or ``value``.

        Returns:
            The updated context record.

        Raises:
            ContextNotFoundError: If the vault reports the context missing.
            ContextAlreadyExistsError: If the vault reports a value conflict.
        """
        try:
            return await self._client.update(namespace, name, patch)
        except VaultError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise ContextNotFoundError(namespace, name) from exc
            if exc.status_code == HTTP_CONFLICT:
                raise ContextAlreadyExistsError(exc.detail) from exc
            raise

    async def delete(self, namespace: str, name: str) -> None:
        """Delete a context via the vault.

        Args:
            namespace: Namespace owning the context.
            name: Context name.

        Raises:
            ContextNotFoundError: If the vault reports the context missing.
        """
        try:
            await self._client.delete(namespace, name)
        except VaultError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise ContextNotFoundError(namespace, name) from exc
            raise

    async def aclose(self) -> None:
        """Close the outbound vault connection pool."""
        await self._client.aclose()
