# worker-env

Ultra-lightweight CSV-based secret distribution on Cloudflare Workers free tier.
Deploy secrets via `wrangler deploy` with zero infrastructure overhead.

## Features

- **CSV-based storage**: Edit `worker/secrets/*.csv` files locally, deploy instantly
- **Context isolation**: Separate contexts per app (homelab, finance, yuktiai, …), each overlaying the shared default context
- **Bearer token auth**: Simple API authentication
- **Read-only distribution**: Update via CSV edit + redeploy (no database writes)
- **Zero infrastructure**: Cloudflare Workers free tier (100K requests/day)
- **Python library**: Import and use in your applications
- **Simple CLI**: Query secrets from your terminal
- **Fast & lightweight**: Python Worker, <100ms latency, native Cloudflare telemetry

## Architecture

```
worker/secrets/
  ├── secrets.csv              # Default context
  ├── homelab.secrets.csv      # Homelab context
  ├── finance.secrets.csv      # Finance context
  └── commerce.secrets.csv     # Commerce context
           │
           │ npm run deploy   (wrangler bundles Worker + CSVs)
           ▼
    Cloudflare Worker (Python, worker/index.py)
           │
           │ HTTPS / Bearer token
           ▼
    Python client / CLI
```

Single-layer design: ~230 lines of Python, no KV, no database, Cloudflare-native telemetry.

## Installation

```bash
# Add to a project
uv add worker-env

# Or install globally for the vault CLI
uv tool install worker-env
```

## Quick Start - Python Library

```python
from worker_env import SecretClient

# Initialize the client
client = SecretClient(
    base_url="https://vault.example.workers.dev",
    token="your-bearer-token"
)

# Retrieve a secret from a context
secret = client.get("homelab", "github-token")
print(secret.value)        # ghp_xxxxxxxxxxxx
print(secret.label)        # GitHub API token for homelab
print(secret.update_count) # 3

# Retrieve from default context
secret = client.get(None, "example-key")
print(secret.value)

# List secrets in a context
secrets = client.list("homelab")
for s in secrets:
    print(f"{s.context}/{s.key}: {s.label}")

# List all secrets
all_secrets = client.list()

# Use context manager for automatic cleanup
with SecretClient(base_url="...", token="...") as client:
    secret = client.get("finance", "stripe-api-key")
    print(secret.value)
```

**Note**: `set()` and `delete()` are not supported in CSV mode. Edit CSV files and redeploy instead.

See [examples/](examples/) for more usage patterns.

## Quick Start - Deployment

### 1. Create Your Secrets

```bash
# CSV files live in worker/secrets/ (git-ignored except .gitkeep)
# Format: key,value,label,update_count,updated_at

# worker/secrets/homelab.secrets.csv
echo "github-token,ghp_your_token,GitHub API token,1,$(date -Iseconds)" > worker/secrets/homelab.secrets.csv

# worker/secrets/finance.secrets.csv
echo "stripe-key,sk_test_your_key,Stripe API key,1,$(date -Iseconds)" > worker/secrets/finance.secrets.csv

# worker/secrets/secrets.csv (default context)
echo "default-key,default-value,Example default secret,1,$(date -Iseconds)" > worker/secrets/secrets.csv
```

### 2. Deploy to Cloudflare

```bash
npm install

# Generate a bearer token
uv run vault gentoken

# Store the token as a Worker secret (never commit it)
npx wrangler secret put VAULT_BEARER_TOKEN

# Deploy Worker + CSV files (~10 seconds)
npm run deploy
```

### 3. Configure Environment

```bash
export VAULT_URL="https://worker-env-vault.<your-subdomain>.workers.dev"
export VAULT_TOKEN="<your-bearer-token>"

# Or store in ~/docker/stack/.env
echo "VAULT_URL=$VAULT_URL" >> ~/docker/stack/.env
echo "VAULT_TOKEN=$VAULT_TOKEN" >> ~/docker/stack/.env
```

### 4. Use the CLI or Python Library

**CLI:**

```bash
# Retrieve a secret from homelab context
uv run vault get homelab/github-token

# Retrieve from default context
uv run vault get example-key

# List all secrets
uv run vault list

# List secrets in a specific context
uv run vault list homelab
```

**Python:**

```python
from worker_env import SecretClient

client = SecretClient(
    base_url="https://vault.example.workers.dev",
    token="your-bearer-token"
)

# Get from named context
secret = client.get("homelab", "github-token")
print(secret.value)

# Get from default context
secret = client.get(None, "example-key")

# List all secrets
secrets = client.list()

# List context-specific secrets
homelab_secrets = client.list("homelab")
```

## HTTP API

All endpoints (except `/health`) require `Authorization: Bearer <token>` header.

| Method | Path                         | Description                                    |
| ------ | ---------------------------- | ---------------------------------------------- |
| GET    | `/health`                    | Unauthenticated health check                   |
| GET    | `/secrets?context=<ctx>`     | List secrets (optional context filter)         |
| GET    | `/secrets/{context}/{key}`   | Retrieve secret from named context             |
| GET    | `/secrets/{key}`             | Retrieve secret from default context           |

**Note**: PUT and DELETE are not supported in CSV-based mode. Edit CSV files and redeploy.

### Example: Direct HTTP Access

```bash
# Health check
curl https://worker-env-vault.<your-subdomain>.workers.dev/health

# Retrieve secret from homelab context
curl https://worker-env-vault.<your-subdomain>.workers.dev/secrets/homelab/github-token \
  -H "Authorization: Bearer <token>"

# Retrieve from default context
curl https://worker-env-vault.<your-subdomain>.workers.dev/secrets/example-key \
  -H "Authorization: Bearer <token>"

# List all secrets
curl https://worker-env-vault.<your-subdomain>.workers.dev/secrets \
  -H "Authorization: Bearer <token>"

# List homelab secrets only
curl "https://worker-env-vault.<your-subdomain>.workers.dev/secrets?context=homelab" \
  -H "Authorization: Bearer <token>"
```

## Configuration

### Worker Authentication

Set the bearer token as a **Wrangler secret** (never commit it):

```bash
npx wrangler secret put VAULT_BEARER_TOKEN
```

A plaintext `[vars]` placeholder is deliberately avoided so it cannot collide
with the encrypted secret on deploy. The vault fails closed: while the token
is unset, every request returns 401.

For local development, put the token in `.dev.vars` at the repo root (git-ignored):

```bash
echo "VAULT_BEARER_TOKEN=<your-token>" > .dev.vars
```

### CLI Environment Variables

| Variable     | Purpose                                                     |
| ------------ | ----------------------------------------------------------- |
| `VAULT_URL`  | Base URL of the deployed Worker                            |
| `VAULT_TOKEN`| Bearer token for authentication                             |

Store tokens in `~/docker/stack/.env`; never commit them.

### CSV File Format

Each CSV file in `worker/secrets/` should have these columns:

```csv
key,value,label,update_count,updated_at
github-token,ghp_xxx,GitHub API token,1,2026-10-08T10:30:00Z
```

- **key**: Secret identifier (unique within the context)
- **value**: The actual secret content
- **label**: Human-readable description
- **update_count**: Number of times updated (increment manually)
- **updated_at**: ISO 8601 timestamp of last update

### File Naming

- `secrets.csv` → default context
- `{context}.secrets.csv` → named contexts (e.g., `homelab.secrets.csv`, `finance.secrets.csv`)

Name each app's context after the app itself — the repo or product name,
lowercase and joined, e.g. `yuktiai.secrets.csv` → context `yuktiai`.

**Default-context fallback.** Named contexts overlay the default context: a
lookup for `{context}/{key}` returns the value defined in that context, or the
default (`secrets.csv`) value for the same key when the named context does not
define it. A value in the named context always wins over the default. This lets
you keep shared keys in `secrets.csv` and override them per app only where
needed.

## Telemetry

Cloudflare-native observability is enabled by default in `wrangler.toml`:

```toml
[observability]
enabled = true
```

View logs and analytics in the Cloudflare dashboard. No custom KV-based analytics needed.

## Local Development

Test the Worker locally before deploying. All commands run from the repo root:

```bash
npm run dev

# In another terminal
export VAULT_URL=http://127.0.0.1:8787
export VAULT_TOKEN="<your-bearer-token>"   # from .dev.vars
uv run vault list
uv run vault get homelab/github-token
```

CSV edits are picked up live by `wrangler dev` — no restart needed.

## Updating Secrets

Since this is a CSV-based, read-only vault, updates follow this workflow:

1. **Edit the CSV file** in `worker/secrets/`
   ```bash
   # Add a new secret
   echo "new-key,new-value,New secret,1,$(date -Iseconds)" >> worker/secrets/homelab.secrets.csv

   # Or edit existing entries manually
   vim worker/secrets/finance.secrets.csv
   ```

2. **Increment `update_count`** if modifying an existing secret

3. **Update `updated_at`** timestamp to current time

4. **Redeploy** (from the repo root)
   ```bash
   npm run deploy
   ```

Changes are live within seconds.

## API Reference

### Python Client

```python
from worker_env import SecretClient, Secret, VaultError

# Initialize client
client = SecretClient(base_url: str, token: str, timeout: float = 10.0)

# Methods
secret: Secret = client.get(context: str | None, key: str)
secrets: list[Secret] = client.list(context: str | None = None)
client.close()

# Note: set() and delete() are not supported in CSV mode

# Secret model
secret.context       # str - Context name (from CSV filename)
secret.key           # str - Secret identifier
secret.value         # str - Secret content
secret.label         # str - Human-readable description
secret.update_count  # int - Number of updates
secret.updated_at    # str - ISO 8601 timestamp

# Exceptions
VaultError              # Base exception
SecretNotFoundError     # HTTP 404
AuthenticationError     # HTTP 401
```

See [examples/](examples/) for complete usage patterns.

### CLI Reference

```bash
uv run vault --help

# Commands
vault gentoken                    # Generate a random bearer token
vault list [context]              # List secrets (optionally filtered by context)
vault get <context>/<key>         # Retrieve a secret value from named context
vault get <key>                   # Retrieve a secret from default context

# Note: set and delete are not supported in CSV mode
vault set <context>/<key> [value] # Not supported (edit CSV + redeploy)
vault delete <context>/<key>      # Not supported (edit CSV + redeploy)

# Global options
--url <url>      # Vault URL (default: $VAULT_URL)
--token <token>  # Bearer token (default: $VAULT_TOKEN)
```

## Development

```bash
uv sync                                        # install dependencies
uv run ruff format src tests examples worker   # format Python code
uv run ruff check src tests examples worker    # lint Python code
uv run mypy src                                # type-check the library
uv run pytest                                  # run tests
uv run vault gentoken                          # test CLI
npm run dev                                    # test Worker locally (repo root)
```

## Why CSV Instead of KV?

This design prioritizes simplicity for your use case (~200 reads/day):

**Advantages:**
- No KV namespace setup required
- Version control your workflow (CSV schema in git, all CSVs ignored via `.gitignore`)
- Instant deploys with `wrangler deploy`
- Zero write operations = simpler code
- Easier to audit (plain text CSV)
- No risk of hitting KV write limits

**Trade-offs:**
- Read-only (updates require redeploy)
- Secrets cached in memory: the default context at cold start, each named context on first access
- Not suitable for >1000 secrets (memory constraints)

For your workload (few hundred secrets, infrequent changes), CSV is the optimal choice.
