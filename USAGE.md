# Using worker-env Vault in Your Projects

This vault provides fast, namespace-isolated secret storage on Cloudflare Workers.
Use it to retrieve API keys, tokens, and credentials in your applications.

## Quick Reference

```bash
# Retrieve a secret (prints value to stdout)
vault get <namespace>/<key>

# Common namespaces: homelab, volunteer, personal
vault get homelab/github-api-key
vault get volunteer/slack-webhook
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
GITHUB_TOKEN=$(vault get homelab/github-api-key)
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user
```

**Python:**
```python
import subprocess
import httpx
import os

# Option A: Via CLI
def get_secret_cli(namespace: str, key: str) -> str:
    result = subprocess.run(
        ["vault", "get", f"{namespace}/{key}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()

# Option B: Direct HTTP
def get_secret_http(namespace: str, key: str) -> str:
    response = httpx.get(
        f"{os.environ['VAULT_URL']}/secrets/{namespace}/{key}",
        headers={"Authorization": f"Bearer {os.environ['VAULT_TOKEN']}"},
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()["value"]

# Usage
github_token = get_secret_cli("homelab", "github-api-key")
db_password = get_secret_http("homelab", "postgres-password")
```

**Node.js/TypeScript:**
```typescript
import { $ } from "bun"; // or use child_process.execSync

// Option A: Via CLI
async function getSecretCLI(namespace: string, key: string): Promise<string> {
  const result = await $`vault get ${namespace}/${key}`.text();
  return result.trim();
}

// Option B: Direct HTTP
async function getSecretHTTP(namespace: string, key: string): Promise<string> {
  const response = await fetch(
    `${process.env.VAULT_URL}/secrets/${namespace}/${key}`,
    { headers: { Authorization: `Bearer ${process.env.VAULT_TOKEN}` } }
  );
  const data = await response.json();
  return data.value;
}

// Usage
const githubToken = await getSecretCLI("homelab", "github-api-key");
const dbUrl = await getSecretHTTP("homelab", "postgres-url");
```

### 3. HTTP API (Direct Access)

All endpoints require `Authorization: Bearer <token>` header.

```bash
# GET secret value
curl https://worker-env-vault.<your>.workers.dev/secrets/homelab/github-api-key \
  -H "Authorization: Bearer $VAULT_TOKEN"

# Response: {"namespace":"homelab","key":"github-api-key","value":"ghp_xxx","updated_at":"2026-10-07T..."}
```

## Secret Management

### Create/Update Secrets

```bash
# Interactive (will prompt for value)
vault set homelab/new-api-key

# Inline value
vault set homelab/github-token "ghp_xxxxxxxxxxxx"

# From stdin
echo "secret-value" | vault set homelab/api-key

# From file
cat ~/.ssh/deploy_key | vault set homelab/ssh-deploy-key
```

### List Secrets

```bash
# List all secrets
vault list

# List by namespace
vault list homelab
vault list volunteer
```

### Delete Secrets

```bash
vault delete homelab/old-api-key
```

## Best Practices

### Namespace Organization

- **homelab** - Personal infrastructure secrets (databases, self-hosted services)
- **volunteer** - Volunteer work credentials (separate from personal)
- **personal** - Individual accounts (GitHub, cloud providers)
- **<project>** - Project-specific secrets (e.g., `myapp-prod`, `myapp-dev`)

### Naming Conventions

Use descriptive, hierarchical keys:

```bash
# Good
vault set homelab/postgres-primary-password "..."
vault set homelab/github-api-token "..."
vault set volunteer/slack-webhook-alerts "..."

# Avoid
vault set homelab/pass "..."
vault set homelab/token1 "..."
```

### Security Notes

1. **Never commit `VAULT_TOKEN` to git** - Store in `~/docker/stack/.env` or encrypted secrets
2. **Use namespaces for isolation** - Separate contexts reduce blast radius
3. **Rotate tokens periodically** - Generate new token with `vault gentoken`
4. **Audit secret access** - Enable `VAULT_ENABLE_ANALYTICS` in Worker config if needed
5. **Prefer direct HTTP in production** - Avoid subprocess overhead in hot paths

## Performance

- **Latency**: <50ms p50 (single Cloudflare KV read)
- **Caching**: Implement application-level caching for frequently accessed secrets
- **Rate limits**: Cloudflare Workers free tier: 100,000 reads/day

## Troubleshooting

```bash
# Test connectivity
vault list

# Common errors
Error: --url or $VAULT_URL is required
  → Set VAULT_URL environment variable

Error: 401 - Unauthorized
  → Check VAULT_TOKEN is correct

Error: 404 - Secret 'namespace/key' not found
  → Verify secret exists with: vault list namespace
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
