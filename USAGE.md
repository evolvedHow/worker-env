# Using worker-env Vault in Your Projects

This vault provides fast, context-isolated secret distribution on Cloudflare
Workers. Secrets live in CSV files, load into memory on the Worker, and are
served read-only over a JSON API.

## Quick Reference

```bash
# Retrieve a secret (prints value to stdout)
vault get <context>/<key>

# Retrieve from the default context (secrets.csv)
vault get <key>

# Common contexts: homelab, finance, commerce
vault get homelab/github-token
vault get finance/stripe-key
vault get example-key
```

## Setup in Your Project

### 1. Environment Configuration

Add to your project's environment or shell rc:

```bash
export VAULT_URL="https://worker-env-vault.<your-subdomain>.workers.dev"
export VAULT_TOKEN="<your-bearer-token>"
```

Or load from a shared env file:

```bash
source ~/docker/stack/.env  # Contains VAULT_URL and VAULT_TOKEN
```

### 2. Retrieve Secrets in Scripts

**Shell scripts:**
```bash
#!/bin/bash
GITHUB_TOKEN=$(vault get homelab/github-token)
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user
```

**Python:**
```python
import os
import subprocess

import httpx

# Option A: Via CLI
def get_secret_cli(context: str, key: str) -> str:
    """Retrieve a secret value via the vault CLI.

    Args:
        context: Context name (None for default context).
        key: Secret key.

    Returns:
        The secret value as a string.
    """
    path = f"{context}/{key}" if context else key
    result = subprocess.run(
        ["vault", "get", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# Option B: Direct HTTP
def get_secret_http(context: str, key: str) -> str:
    """Retrieve a secret value directly from the Worker API.

    Args:
        context: Context name (None for default context).
        key: Secret key.

    Returns:
        The secret value as a string.
    """
    base_url = os.environ["VAULT_URL"]
    path = f"/secrets/{context}/{key}" if context else f"/secrets/{key}"
    response = httpx.get(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {os.environ['VAULT_TOKEN']}"},
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()["value"]


# Usage
github_token = get_secret_cli("homelab", "github-token")
db_password = get_secret_http("homelab", "postgres-password")
```

**Python client library:**
```python
import os

from worker_env import SecretClient

with SecretClient(
    base_url=os.environ["VAULT_URL"],
    token=os.environ["VAULT_TOKEN"],
) as client:
    secret = client.get("homelab", "github-token")
    print(secret.value)
    print(secret.label)
    print(secret.update_count)
```

**Node.js/TypeScript:**
```typescript
// Option A: Via CLI
async function getSecretCLI(context: string, key: string): Promise<string> {
  const result = await $`vault get ${context}/${key}`.text();
  return result.trim();
}

// Option B: Direct HTTP
async function getSecretHTTP(context: string, key: string): Promise<string> {
  const response = await fetch(
    `${process.env.VAULT_URL}/secrets/${context}/${key}`,
    { headers: { Authorization: `Bearer ${process.env.VAULT_TOKEN}` } }
  );
  const data = await response.json();
  return data.value;
}

// Usage
const githubToken = await getSecretCLI("homelab", "github-token");
const dbUrl = await getSecretHTTP("homelab", "postgres-url");
```

### 3. HTTP API (Direct Access)

All endpoints require `Authorization: Bearer <token>` header.

```bash
# GET secret from a context
curl https://worker-env-vault.<your>.workers.dev/secrets/homelab/github-token \
  -H "Authorization: Bearer $VAULT_TOKEN"

# Response:
# {"context":"homelab","key":"github-token","value":"ghp_xxx",
#  "label":"GitHub API token","update_count":1,
#  "updated_at":"2026-10-08T10:30:00Z"}

# GET secret from default context
curl https://worker-env-vault.<your>.workers.dev/secrets/example-key \
  -H "Authorization: Bearer $VAULT_TOKEN"

# LIST secrets (optionally filtered by context)
curl "https://worker-env-vault.<your>.workers.dev/secrets?context=homelab" \
  -H "Authorization: Bearer $VAULT_TOKEN"
```

## Secret Management

The vault is **read-only at runtime**. Secrets are managed by editing CSV
files in the `worker/secrets/` directory and redeploying with `npm run deploy`.

### Add or Update Secrets

```bash
# Add a new secret to a context
echo "new-key,new-value,My label,1,$(date -Iseconds)" \
  >> worker/secrets/homelab.secrets.csv

# Update an existing secret: increment update_count, bump updated_at
# Edit the row directly:
#   github-token,ghp_new_token,GitHub API token,2,2026-10-08T12:00:00Z

# Add to the default context
echo "global-key,value,Label,1,$(date -Iseconds)" >> worker/secrets/secrets.csv

# Deploy from the repo root (takes ~10 seconds)
npm run deploy
```

### List Secrets

```bash
# List all secrets
vault list

# List by context
vault list homelab
vault list finance
```

### Delete Secrets

```bash
# Remove the row from the CSV file, then redeploy
vim worker/secrets/homelab.secrets.csv
npm run deploy
```

## Best Practices

### Context Organization

- **homelab** - Personal infrastructure secrets (databases, self-hosted services)
- **finance** - Financial service credentials (Stripe, Plaid, banking APIs)
- **commerce** - Storefront and payment credentials (Shopify, email providers)
- **<project>** - Project-specific secrets (e.g., `myapp-prod`, `myapp-dev`)
- **default** (`secrets.csv`) - Shared secrets used across contexts

### Naming Conventions

Use descriptive, hierarchical keys:

```bash
# Good
postgres-primary-password  ...  Primary PostgreSQL password
github-api-token           ...  GitHub API token for automation
slack-webhook-alerts       ...  Slack webhook for alert notifications

# Avoid
pass                       ...  ambiguous
token1                     ...  meaningless
```

### Security Notes

1. **Never commit `VAULT_TOKEN` to git** - Store in `~/docker/stack/.env` or encrypted secrets
2. **Never commit real CSV files** - `.gitignore` excludes `worker/secrets/*.csv`; keep
   real secrets in `~/docker/stack/secrets/` and copy into `worker/secrets/` before deploy
3. **Use contexts for isolation** - Separate contexts reduce blast radius
4. **Rotate tokens periodically** - Generate new token with `vault gentoken`,
   update with `npx wrangler secret put VAULT_BEARER_TOKEN`, redeploy
5. **Prefer direct HTTP in production** - Avoid subprocess overhead in hot paths

## Performance

- **Latency**: <50ms p50 (single Cloudflare Worker read from memory)
- **Caching**: Implement application-level caching for frequently accessed secrets
- **Rate limits**: Cloudflare Workers free tier: 100,000 requests/day

## Troubleshooting

```bash
# Test connectivity
vault list

# Common errors
Error: --url or $VAULT_URL is required
  → Set VAULT_URL environment variable

Error: 401 - Unauthorized
  → Check VAULT_TOKEN is correct; redeploy if VAULT_BEARER_TOKEN changed

Error: 404 - Secret 'context/key' not found
  → Verify secret exists with: vault list <context>
  → Check the CSV filename matches the context (e.g., homelab.secrets.csv)
```

## Integration Examples

### Docker Compose

```yaml
services:
  app:
    image: myapp:latest
    environment:
      DATABASE_URL: ${DATABASE_URL}
    command: >
      sh -c "
        export DATABASE_URL=$$(vault get homelab/postgres-url) &&
        exec python app.py
      "
```

### systemd Service

```ini
[Service]
Type=simple
EnvironmentFile=/etc/myapp/vault.env
ExecStartPre=/usr/local/bin/vault get homelab/api-key > /run/myapp/api-key
ExecStart=/usr/local/bin/myapp --api-key-file /run/myapp/api-key
```

### GitHub Actions

```yaml
steps:
  - name: Retrieve secrets
    run: |
      echo "GITHUB_TOKEN=$(vault get homelab/github-api-key)" >> $GITHUB_ENV
    env:
      VAULT_URL: ${{ secrets.VAULT_URL }}
      VAULT_TOKEN: ${{ secrets.VAULT_TOKEN }}

  - name: Use secret
    run: gh api /user
    env:
      GITHUB_TOKEN: ${{ env.GITHUB_TOKEN }}
```

---

**Vault URL**: Ask the vault administrator for `VAULT_URL` and `VAULT_TOKEN`
**CLI Tool**: `uv run vault` (requires Python + uv) or install globally with `uv tool install worker-env`
