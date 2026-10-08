# worker-env Vault Cheat Sheet

Fast secret retrieval for homelab and volunteer projects.

## Setup

```bash
export VAULT_URL="https://worker-env-vault.<your>.workers.dev"
export VAULT_TOKEN="<your-bearer-token>"
```

## CLI Commands

```bash
vault get <namespace>/<key>              # Retrieve secret (prints value)
vault set <namespace>/<key> [value]      # Create/update (reads stdin if no value)
vault list [namespace]                   # List secrets
vault delete <namespace>/<key>           # Delete secret
vault gentoken                           # Generate new bearer token
```

## Common Namespaces

- `homelab` - Personal infrastructure (databases, services)
- `volunteer` - Volunteer work credentials
- `personal` - Individual accounts

## Usage in Code

### Shell
```bash
GITHUB_TOKEN=$(vault get homelab/github-api-key)
DB_URL=$(vault get homelab/postgres-url)
```

### Python
```python
import subprocess
import os

def get_secret(namespace: str, key: str) -> str:
    result = subprocess.run(
        ["vault", "get", f"{namespace}/{key}"],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()

# Direct HTTP (faster)
import httpx
response = httpx.get(
    f"{os.environ['VAULT_URL']}/secrets/{namespace}/{key}",
    headers={"Authorization": f"Bearer {os.environ['VAULT_TOKEN']}"}
)
secret_value = response.json()["value"]
```

### TypeScript/Node.js
```typescript
// CLI approach
const secret = await $`vault get homelab/api-key`.text();

// Direct HTTP (faster)
const response = await fetch(
  `${process.env.VAULT_URL}/secrets/${namespace}/${key}`,
  { headers: { Authorization: `Bearer ${process.env.VAULT_TOKEN}` } }
);
const { value } = await response.json();
```

## HTTP API

```bash
# GET secret
curl $VAULT_URL/secrets/homelab/github-api-key \
  -H "Authorization: Bearer $VAULT_TOKEN"

# PUT secret
curl -X PUT $VAULT_URL/secrets/homelab/new-key \
  -H "Authorization: Bearer $VAULT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value":"secret-value"}'

# DELETE secret
curl -X DELETE $VAULT_URL/secrets/homelab/old-key \
  -H "Authorization: Bearer $VAULT_TOKEN"

# LIST secrets
curl "$VAULT_URL/secrets?namespace=homelab" \
  -H "Authorization: Bearer $VAULT_TOKEN"
```

## Examples

```bash
# Store API keys
vault set homelab/github-api-key "ghp_xxxxxxxxxxxx"
vault set volunteer/slack-webhook "https://hooks.slack.com/..."

# Retrieve in scripts
export GH_TOKEN=$(vault get homelab/github-api-key)
gh api /user

# From file
cat ~/.ssh/deploy_key | vault set homelab/ssh-deploy-key

# List all homelab secrets
vault list homelab
```

## Tips

- **Prefer direct HTTP** in production (no subprocess overhead)
- **Cache frequently used secrets** in memory
- **Use descriptive keys**: `postgres-primary-password` not `pass1`
- **Never commit tokens** - store in `~/docker/stack/.env`
- **Latency**: <50ms p50
- **Free tier**: 100K reads/day

## Troubleshooting

```bash
vault list                    # Test connectivity
echo $VAULT_URL $VAULT_TOKEN  # Verify env vars set
```
