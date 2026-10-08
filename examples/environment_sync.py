"""Load secrets from vault into environment variables."""

import os

from worker_env import SecretClient


def load_context_to_env(context: str) -> None:
    """Load all secrets from a context into environment variables.

    Secret keys are uppercased and prefixed with the context.
    Example: homelab/api-key becomes HOMELAB_API_KEY

    Args:
        context: Vault context to load secrets from.
    """
    client = SecretClient(
        base_url=os.environ["VAULT_URL"],
        token=os.environ["VAULT_TOKEN"],
    )

    secrets = client.list(context)
    print(f"Loading {len(secrets)} secrets from '{context}' context...")

    for secret in secrets:
        # Convert to environment variable name
        env_var = f"{context.upper()}_{secret.key.upper().replace('-', '_')}"
        os.environ[env_var] = secret.value
        print(f"  Loaded: {env_var}")

    client.close()


def sync_specific_secrets(mapping: dict[str, tuple[str | None, str]]) -> None:
    """Sync specific vault secrets to environment variables.

    Args:
        mapping: Dict of {env_var_name: (context, key)} pairs. Use context
            None for the default context (secrets/secrets.csv).

    Example:
        >>> sync_specific_secrets({
        ...     "GITHUB_TOKEN": ("homelab", "github-token"),
        ...     "DB_PASSWORD": ("homelab", "postgres-password"),
        ... })
    """
    client = SecretClient(
        base_url=os.environ["VAULT_URL"],
        token=os.environ["VAULT_TOKEN"],
    )

    for env_var, (context, key) in mapping.items():
        try:
            secret = client.get(context, key)
            os.environ[env_var] = secret.value
            print(f"Synced {context}/{key} -> ${env_var}")
        except Exception as e:
            print(f"Failed to load {context}/{key}: {e}")

    client.close()


if __name__ == "__main__":
    # Example 1: Load entire context
    load_context_to_env("homelab")

    # Example 2: Sync specific secrets
    sync_specific_secrets(
        {
            "GITHUB_TOKEN": ("homelab", "github-token"),
            "OPENAI_API_KEY": (None, "openai-key"),
        }
    )

    # Now use the loaded secrets
    print(f"\nGitHub token loaded: {os.environ.get('GITHUB_TOKEN', 'NOT_FOUND')}")
