"""Domain errors raised by context stores and the vault client."""

HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409


class WorkerEnvError(Exception):
    """Base class for all worker-env domain errors."""


class ContextNotFoundError(WorkerEnvError):
    """Raised when a requested context does not exist."""

    def __init__(self, namespace: str, name: str) -> None:
        """Build the not-found error for a context key.

        Args:
            namespace: Namespace that should own the context.
            name: Context name that was requested.
        """
        super().__init__(f"context '{namespace}/{name}' not found")


class ContextAlreadyExistsError(WorkerEnvError):
    """Raised when creation or update would violate a uniqueness rule."""


class VaultError(WorkerEnvError):
    """Raised when the vault returns an HTTP error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Wrap a vault error response.

        Args:
            status_code: HTTP status code returned by the vault.
            detail: Human-readable error detail returned by the vault.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
