"""worker-env: Minimal secret vault on Cloudflare Workers.

This package provides a Python client and CLI for reading secrets from a
CSV-backed Cloudflare Worker vault with context isolation and bearer token
authentication. The vault is read-only: secrets are updated by editing the
CSV files under worker/secrets/ and redeploying the Worker.

Example:
    >>> from worker_env import SecretClient
    >>>
    >>> client = SecretClient(
    ...     base_url="https://vault.example.workers.dev",
    ...     token="your-bearer-token",
    ... )
    >>>
    >>> # Retrieve a secret from a named context
    >>> secret = client.get("homelab", "github-token")
    >>> print(secret.value)
    >>>
    >>> # List all secrets in a context
    >>> secrets = client.list("homelab")
    >>> for s in secrets:
    ...     print(f"{s.context}/{s.key}")
"""

from importlib.metadata import PackageNotFoundError, version

from worker_env.client import SecretClient
from worker_env.errors import AuthenticationError, SecretNotFoundError, VaultError
from worker_env.models import Secret, SecretList

try:
    __version__ = version("worker-env")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "AuthenticationError",
    "Secret",
    "SecretClient",
    "SecretList",
    "SecretNotFoundError",
    "VaultError",
    "__version__",
]
