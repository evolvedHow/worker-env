"""HTTP client for the read-only Cloudflare Worker vault."""

from typing import Self

import httpx

from worker_env.errors import AuthenticationError, SecretNotFoundError, VaultError
from worker_env.models import Secret, SecretList

# HTTP status codes
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
SECRETS_PATH_PREFIX = "/secrets/"

# Path segment counts for "/secrets/..." request paths
NAMED_CONTEXT_PATH_SEGMENTS = 3
DEFAULT_CONTEXT_PATH_SEGMENTS = 2


class SecretClient:
    """Read-only synchronous HTTP client for the vault.

    This client provides a Python interface to the Cloudflare Worker vault API,
    using bearer token authentication for all requests. The vault is read-only:
    it only exposes ``get()`` and ``list()``.

    Example:
        >>> client = SecretClient(
        ...     base_url="https://vault.example.workers.dev",
        ...     token="your-bearer-token",
        ... )
        >>> secret = client.get("homelab", "github-token")
        >>> print(secret.value)
        ghp_...
        >>> for item in client.list("homelab"):
        ...     print(f"{item.context}/{item.key}")

    Attributes:
        base_url: Base URL of the deployed vault Worker.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        """Create a vault client with bearer token authentication.

        Args:
            base_url: Base URL of the vault (e.g., "https://vault.workers.dev").
            token: Bearer token for authentication.
            timeout: HTTP request timeout in seconds (default: 10.0).
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token = token
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> Self:
        """Support context manager protocol."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the HTTP client on context manager exit."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool.

        It's recommended to use the client as a context manager to ensure
        proper cleanup, or explicitly call this method when done.
        """
        self._client.close()

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send an authenticated GET request to the vault.

        Args:
            path: Request path (e.g., "/secrets/homelab/key").
            params: Optional query parameters.

        Returns:
            The HTTP response object.

        Raises:
            VaultError: If the vault returns an error response.
            AuthenticationError: If authentication fails (401).
            SecretNotFoundError: If a secret is not found (404).
        """
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}

        try:
            response = self._client.get(url, headers=headers, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = self._extract_detail(exc.response)

            if status_code == HTTP_UNAUTHORIZED:
                raise AuthenticationError(detail) from exc
            if status_code == HTTP_NOT_FOUND and path.startswith(SECRETS_PATH_PREFIX):
                # Strip the leading slash and split into segments; the first
                # segment is always the literal "secrets" resource name.
                parts = path.strip("/").split("/")
                if len(parts) == NAMED_CONTEXT_PATH_SEGMENTS:
                    raise SecretNotFoundError(parts[1], parts[2]) from exc
                if len(parts) == DEFAULT_CONTEXT_PATH_SEGMENTS:
                    raise SecretNotFoundError(None, parts[1]) from exc
            raise VaultError(status_code, detail) from exc
        except httpx.RequestError as exc:
            raise VaultError(0, f"Connection error: {exc}") from exc
        else:
            return response

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        """Extract error detail from a vault response.

        Args:
            response: HTTP error response from the vault.

        Returns:
            Human-readable error message.
        """
        try:
            data = response.json()
            if isinstance(data, dict) and "detail" in data:
                return str(data["detail"])
        except (ValueError, KeyError):
            # JSON decode failed or detail missing - fall through to default
            pass
        return f"HTTP {response.status_code}"

    def get(self, context: str | None, key: str) -> Secret:
        """Retrieve a secret from the vault.

        Args:
            context: Context containing the secret (None for default context).
            key: Secret key to retrieve.

        Returns:
            The secret with its metadata.

        Note:
            Named contexts overlay the default (blank) context. When ``key`` is
            absent from ``context``, the vault serves the default-context value
            if one exists; a named-context value always wins. A 404 therefore
            means the key is defined in neither context. Inspect
            ``secret.context`` to see which context supplied the value.

        Raises:
            SecretNotFoundError: If the secret exists in neither context.
            AuthenticationError: If the bearer token is invalid.
            VaultError: If the vault returns any other error.

        Example:
            >>> secret = client.get("homelab", "github-token")
            >>> print(f"Value: {secret.value}")
            >>> print(f"Label: {secret.label}")
            >>> print(f"Updated: {secret.updated_at}")
            >>>
            >>> # Get from default context
            >>> secret = client.get(None, "example-key")
        """
        path = f"/secrets/{context}/{key}" if context else f"/secrets/{key}"

        response = self._get(path)
        return Secret.model_validate(response.json())

    def list(self, context: str | None = None) -> list[Secret]:
        """List secrets, optionally filtered by context.

        Args:
            context: Optional context to filter by. If None, returns all secrets.

        Returns:
            List of secrets matching the query. Returns empty list if no secrets found.

        Raises:
            AuthenticationError: If the bearer token is invalid.
            VaultError: If the vault returns an error.

        Example:
            >>> all_secrets = client.list()
            >>> homelab_secrets = client.list("homelab")
            >>> for secret in homelab_secrets:
            ...     print(f"{secret.context}/{secret.key}: {secret.label}")
        """
        params = {"context": context} if context else None
        response = self._get("/secrets", params=params)
        secret_list = SecretList.model_validate(response.json())
        return secret_list.secrets
