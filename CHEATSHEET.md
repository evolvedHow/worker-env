# worker-env Vault Cheat Sheet

Fast secret retrieval for homelab, finance, and commerce projects.

## Setup

```bash
export VAULT_URL="https://worker-env-vault.<your>.workers.dev"
export VAULT_TOKEN="<your-bearer-token>"
```

## CLI Commands

```bash
vault get <context>/<key>    # Retrieve secret from a context (prints value)
vault get <key>              # Retrieve secret from default context
vault list [context]         # List secrets (all, or filtered by context)
vault gentoken               # Generate new bearer token
```

Read-only at runtime: `set` and `delete` are not supported. Edit CSV files
and redeploy instead.

## Common Contexts

- `homelab` - Personal infrastructure (databases, services)
- `finance` - Financial credentials (Stripe, Plaid)
- `commerce` - Storefront credentials (Shopify, email)
- default - `worker/secrets/secrets.csv` (no context needed)

## CSV Management

```bash
# Add a secret (then redeploy from the repo root)
echo "key,value,label,1,$(date -Iseconds)" >> worker/secrets/homelab.secrets.csv
npm run deploy

# CSV columns: key,value,label,update_count,updated_at
# File naming: secrets.csv (default), <context>.secrets.csv
```

## Usage in Code

### Shell
```bash
GITHUB_TOKEN=$(vault get homelab/github-token)
DB_URL=$(vault get homelab/postgres-url)
```

### Python
```python
import os
import subprocess

import httpx


def get_secret(context: str, key: str) -> str:
    """Retrieve a secret value via the vault CLI.

    Args:
        context: Context name (empty string for default context).
        key: Secret key.

    Returns:
        The secret value as a string.
    """
    path = f"{context}/{key}" if context else key
    result = subprocess.run(
        ["vault", "get", path],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


# Direct HTTP (faster)
response = httpx.get(
    f"{os.environ['VAULT_URL']}/secrets/homelab/github-token",
    headers={"Authorization": f"Bearer {os.environ['VAULT_TOKEN']}"}
)
secret_value = response.json()["value"]

# Client library
from worker_env import SecretClient

with SecretClient(
    base_url=os.environ["VAULT_URL"],
    token=os.environ["VAULT_TOKEN"],
) as client:
    secret = client.get("homelab", "github-token")
    print(secret.value, secret.label, secret.update_count)
```

### TypeScript/Node.js
```typescript
// CLI approach
const secret = await $`vault get homelab/api-key`.text();

// Direct HTTP (faster)
const response = await fetch(
  `${process.env.VAULT_URL}/secrets/homelab/github-token`,
  { headers: { Authorization: `Bearer ${process.env.VAULT_TOKEN}` } }
);
const { value } = await response.json();
```

## HTTP API

```bash
# GET secret from a context
curl $VAULT_URL/secrets/homelab/github-token \
  -H "Authorization: Bearer $VAULT_TOKEN"

# GET secret from default context
curl $VAULT_URL/secrets/example-key \
  -H "Authorization: Bearer $VAULT_TOKEN"

# LIST secrets (filter by context)
curl "$VAULT_URL/secrets?context=homelab" \
  -H "Authorization: Bearer $VAULT_TOKEN"

# Health check (no auth)
curl $VAULT_URL/health
```

## Deploy

```bash
npm install          # first time only (repo root)
npm run deploy       # bundles Worker + worker/secrets/ CSVs
```

## Tips

- **Prefer direct HTTP** in production (no subprocess overhead)
- **Cache frequently used secrets** in memory
- **Use descriptive keys**: `postgres-primary-password` not `pass1`
- **Never commit tokens** - store in `~/docker/stack/.env`
- **Never commit CSVs** - `worker/secrets/*.csv` is git-ignored
- **Latency**: <50ms p50
- **Free tier**: 100K requests/day

## Troubleshooting

```bash
vault list                    # Test connectivity
echo $VAULT_URL $VAULT_TOKEN  # Verify env vars set
vault list homelab            # Check context exists and has secrets
```
