"""Basic usage examples for the worker-env vault client.

The vault is read-only at runtime: secrets are added or edited in CSV files
under secrets/ and deployed with `wrangler deploy`.
"""

import os

from worker_env import SecretClient

# Use a context manager for automatic cleanup. Credentials come from the
# environment (see ~/docker/stack/.env).
with SecretClient(
    base_url=os.environ["VAULT_URL"],
    token=os.environ["VAULT_TOKEN"],
) as client:
    # Retrieve a secret from a named context
    secret = client.get("homelab", "github-token")
    print(f"Secret value: {secret.value}")
    print(f"Label: {secret.label}")
    print(f"Update count: {secret.update_count}")
    print(f"Last updated: {secret.updated_at}")

    # Retrieve a secret from the default context (secrets/secrets.csv)
    secret = client.get(None, "example-key")
    print(f"Default context secret: {secret.value}")

    # List all secrets in a context
    secrets = client.list("homelab")
    print(f"\nFound {len(secrets)} secrets in 'homelab' context:")
    for s in secrets:
        print(f"  - {s.context}/{s.key}: {s.label} (updated: {s.updated_at})")

    # List all secrets across all contexts
    all_secrets = client.list()
    print(f"\nTotal secrets: {len(all_secrets)}")

# To add, edit, or delete a secret, modify the CSV files in worker/secrets/ and
# redeploy from the repo root: npm run deploy
