"""Examples of error handling with the vault client."""

import os
import time

from worker_env import (
    AuthenticationError,
    SecretClient,
    SecretNotFoundError,
    VaultError,
)

client = SecretClient(
    base_url=os.environ["VAULT_URL"],
    token=os.environ["VAULT_TOKEN"],
)


def safe_get_secret(context: str, key: str) -> str | None:
    """Safely retrieve a secret, returning None if not found.

    Args:
        context: Context containing the secret (empty string for default).
        key: Secret key to retrieve.

    Returns:
        Secret value if found, None otherwise.
    """
    try:
        secret = client.get(context, key)
        return secret.value
    except SecretNotFoundError:
        print(f"Secret {context}/{key} not found")
        return None
    except AuthenticationError:
        print("Authentication failed - check your bearer token")
        return None
    except VaultError as e:
        print(f"Vault error: {e.detail} (status: {e.status_code})")
        return None


def get_secret_with_retry(context: str, key: str, retries: int = 3) -> str | None:
    """Retrieve a secret with automatic retry on transient network failures.

    Args:
        context: Context containing the secret (empty string for default).
        key: Secret key to retrieve.
        retries: Number of retry attempts for connection errors.

    Returns:
        Secret value if successful, None otherwise.
    """
    for attempt in range(retries):
        try:
            return client.get(context, key).value
        except AuthenticationError as e:
            print(f"Authentication failed: {e.detail}")
            return None  # Don't retry auth failures
        except SecretNotFoundError as e:
            print(f"Not found: {e.detail}")
            return None  # Don't retry missing secrets
        except VaultError as e:
            print(f"Attempt {attempt + 1}/{retries} failed: {e.detail}")
            if attempt == retries - 1:
                return None
            time.sleep(0.5 * (attempt + 1))
    return None


def get_secret_or_default(context: str, key: str, default: str) -> str:
    """Get a secret or return a default value if not found.

    Args:
        context: Context containing the secret (empty string for default).
        key: Secret key to retrieve.
        default: Default value to return if secret not found.

    Returns:
        Secret value or default.
    """
    try:
        return client.get(context, key).value
    except SecretNotFoundError:
        return default


if __name__ == "__main__":
    # Example 1: Safe retrieval
    api_key = safe_get_secret("homelab", "api-key")
    if api_key:
        print(f"API key: {api_key}")

    # Example 2: Get with retry on network failures
    db_password = get_secret_with_retry("homelab", "postgres-password")
    print(f"Database password loaded: {db_password is not None}")

    # Example 3: Get with default
    db_host = get_secret_or_default("homelab", "db-host", "localhost")
    print(f"Database host: {db_host}")

    client.close()
