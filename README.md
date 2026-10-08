# worker-env

Minimal secret vault on Cloudflare Workers free tier. Fast, namespace-based
secret storage optimized for quick API key retrieval with optional analytics.

## Features

- **Namespace isolation**: Separate homelab, volunteer, and personal secrets
- **Bearer token auth**: Simple service-to-service authentication
- **Fast reads**: No write operations on GET, no signing overhead
- **Optional analytics**: Lightweight daily access tracking (30-day retention)
- **Zero infrastructure**: Cloudflare Workers free tier (100K reads, 1K writes/day)
- **Simple CLI**: Manage secrets from your terminal

## Architecture

```
vault CLI / HTTP client
   │  Bearer token auth
   ▼
Cloudflare Worker (worker/) ──► KV namespace SECRETS
```

Single-layer design: ~230 lines of TypeScript, <100ms latency, no external dependencies.

## Secret Schema

```typescript
{
  namespace: string,  // "homelab" | "volunteer" | "personal"
  key: string,        // "github-api-key"
  value: string,      // the actual secret
  updated_at: string  // ISO timestamp
}
```

Stored in KV as: `{namespace}:{key}` → JSON(Secret)

## Quick Start

### 1. Deploy the Cloudflare Worker

```bash
cd worker
npm install

# Create KV namespace
npx wrangler kv namespace create SECRETS
# Copy the returned id into wrangler.toml [[kv_namespaces]] binding

# Generate bearer token
uv run vault gentoken
# Copy the bearer_token into wrangler.toml [vars] VAULT_BEARER_TOKEN

# Deploy
npm run deploy
```

### 2. Configure CLI

```bash
export VAULT_URL="https://worker-env-vault.<your-subdomain>.workers.dev"
export VAULT_TOKEN="<your-bearer-token>"

# Or store in ~/docker/stack/.env
echo "VAULT_URL=$VAULT_URL" >> ~/docker/stack/.env
echo "VAULT_TOKEN=$VAULT_TOKEN" >> ~/docker/stack/.env
```

### 3. Manage Secrets

```bash
# Create a secret
uv run vault set homelab/github-api-key "ghp_xxxxxxxxxxxx"

# Retrieve a secret
uv run vault get homelab/github-api-key

# List secrets
uv run vault list
uv run vault list homelab

# Delete a secret
uv run vault delete homelab/github-api-key
```

## HTTP API

All endpoints (except `/health`) require `Authorization: Bearer <token>` header.

| Method | Path                         | Description                           |
| ------ | ---------------------------- | ------------------------------------- |
| GET    | `/health`                    | Unauthenticated health check          |
| GET    | `/secrets?namespace=<ns>`    | List secrets (optional namespace filter) |
| GET    | `/secrets/{ns}/{key}`        | Retrieve secret value                 |
| PUT    | `/secrets/{ns}/{key}`        | Create or update secret               |
| DELETE | `/secrets/{ns}/{key}`        | Delete secret                         |

### Example: Direct HTTP Access

```bash
# Create secret
curl -X PUT https://worker-env-vault.<your-subdomain>.workers.dev/secrets/homelab/db-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"value":"super-secret-password"}'

# Retrieve secret
curl https://worker-env-vault.<your-subdomain>.workers.dev/secrets/homelab/db-password \
  -H "Authorization: Bearer <token>"
```

## Configuration

### Worker Environment Variables (wrangler.toml)

| Variable                   | Purpose                                                     |
| -------------------------- | ----------------------------------------------------------- |
| `VAULT_BEARER_TOKEN`       | Bearer token for authentication (required)                  |
| `VAULT_ENABLE_ANALYTICS`   | Set to `"true"` to enable daily access tracking (optional)  |

### CLI Environment Variables

| Variable     | Purpose                                                     |
| ------------ | ----------------------------------------------------------- |
| `VAULT_URL`  | Base URL of the deployed Worker                            |
| `VAULT_TOKEN`| Bearer token for authentication                             |

Store tokens in `~/docker/stack/.env`; never commit them.

## Analytics (Optional)

Enable access tracking by setting `VAULT_ENABLE_ANALYTICS = "true"` in `wrangler.toml [vars]`.

When enabled, each GET request asynchronously records daily access counts:
- KV key: `analytics:{namespace}:{key}:{YYYY-MM-DD}`
- Value: Integer access count
- TTL: 30 days

Analytics writes are non-blocking and failures are silently ignored to maintain
read performance.

## Local Development

Test the Worker locally before deploying:

```bash
cd worker
npx wrangler dev --port 8787

# In another terminal
export VAULT_URL=http://127.0.0.1:8787
export VAULT_TOKEN="<your-bearer-token>"
uv run vault list
```

## CLI Reference

```bash
uv run vault --help

# Commands
vault gentoken                      # Generate a random bearer token
vault list [namespace]              # List secrets (optionally filtered)
vault get <namespace>/<key>         # Retrieve a secret value
vault set <namespace>/<key> [value] # Create/update (reads stdin if value omitted)
vault delete <namespace>/<key>      # Delete a secret

# Global options
--url <url>      # Vault URL (default: $VAULT_URL)
--token <token>  # Bearer token (default: $VAULT_TOKEN)
```

## Development

```bash
uv sync                           # install dependencies
cd worker && npx tsc --noEmit     # type check Worker
uv run ruff check src             # lint CLI
uv run vault gentoken             # test CLI
```
