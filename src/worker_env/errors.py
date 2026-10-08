"""Exceptions raised by the vault client."""


class VaultError(Exception):
    """Base exception for all vault-related errors.

    Attributes:
        status_code: HTTP status code from the vault response.
        detail: Human-readable error message from the vault.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        """Create a vault error from an HTTP response.

        Args:
            status_code: HTTP status code returned by the vault.
            detail: Error message from the vault's response body.
        """
        super().__init__(f"Vault error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class SecretNotFoundError(VaultError):
    """Raised when a requested secret does not exist (HTTP 404)."""

    def __init__(self, context: str | None, key: str) -> None:
        """Create a not-found error for a specific secret.

        Args:
            context: Context that was queried (None for the default context).
            key: Secret key that was not found.
        """
        path = f"{context}/{key}" if context else key
        super().__init__(404, f"Secret '{path}' not found")
        self.context = context
        self.key = key


class AuthenticationError(VaultError):
    """Raised when authentication fails (HTTP 401)."""

    def __init__(self, detail: str = "Invalid or missing bearer token") -> None:
        """Create an authentication error.

        Args:
            detail: Error message describing the authentication failure.
        """
        super().__init__(401, detail)
