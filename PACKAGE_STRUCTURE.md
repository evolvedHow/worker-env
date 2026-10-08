# Package Structure - worker-env

This document describes the package structure of the CSV-based vault.

## Package Layout

```
worker-env/
├── src/worker_env/           # Main package source
│   ├── __init__.py           # Public API exports
│   ├── client.py             # SecretClient class
│   ├── models.py             # Pydantic models (Secret, SecretList, SecretValue)
│   ├── errors.py             # Exception classes
│   ├── cli.py                # CLI implementation
│   └── py.typed              # Type hint marker
├── worker/                   # Cloudflare Python Worker
│   ├── index.py              # Worker entry point (CSV loader + HTTP API)
│   └── secrets/              # CSV storage (git-ignored except .gitkeep)
│       ├── .gitkeep
│       ├── secrets.csv           # Default context
│       ├── homelab.secrets.csv   # Named context example
│       ├── finance.secrets.csv   # Named context example
│       └── commerce.secrets.csv  # Named context example
├── examples/                 # Usage examples
│   ├── basic_usage.py
│   ├── environment_sync.py
│   └── error_handling.py
├── tests/                    # Unit tests
├── wrangler.toml             # Cloudflare config (repo root; wrangler runs from CWD)
├── package.json              # npm scripts: deploy, dev (repo root)
├── pyproject.toml            # Package metadata and build config
└── README.md                 # Full documentation
```

## Public API

The package exports the following public interface:

```python
from worker_env import (
    # Client
    SecretClient,

    # Models
    Secret,
    SecretList,
    SecretValue,

    # Exceptions
    VaultError,
    SecretNotFoundError,
    AuthenticationError,

    # Version
    __version__,
)
```

## Installation

Install via uv:

```bash
# Add to a project
uv add worker-env

# Or install globally for the vault CLI
uv tool install worker-env
```

## CLI Entry Point

The package includes a `vault` CLI command defined in `pyproject.toml`:

```toml
[project.scripts]
vault = "worker_env.cli:main"
```

## Type Hints

The package includes a `py.typed` marker file, making it compatible with type checkers like mypy and pyright.

## Testing

Tests are located in `tests/` and can be run with:

```bash
uv run pytest tests/
```

## Quality Checks

All code passes:
- Ruff linting (`uv run ruff check src/ tests/ examples/ worker/`)
- Ruff formatting (`uv run ruff format src/ tests/ examples/ worker/`)
- MyPy type checking (`uv run mypy src/`)
- Pytest tests (`uv run pytest`)

## Dependencies

**Runtime:**
- `httpx>=0.27` - HTTP client
- `pydantic>=2.9` - Data validation

**Development:**
- `mypy>=1.13` - Type checking
- `pytest>=8.3` - Testing
- `ruff>=0.7` - Linting and formatting

## Schema

Secrets are rows in per-context CSV files:

| Column        | Type | Description                              |
| ------------- | ---- | ---------------------------------------- |
| `key`         | str  | Secret identifier (unique per context)   |
| `value`       | str  | Secret content                           |
| `label`       | str  | Human-readable description               |
| `update_count`| int  | Number of updates (incremented manually) |
| `updated_at`  | str  | ISO 8601 timestamp of last update        |

The `context` field on the `Secret` model is derived from the CSV filename,
not stored as a column: `secrets.csv` → default context,
`homelab.secrets.csv` → "homelab".

## Changes from Original

**Removed:**
- Legacy vault API (api.py, store.py, signing.py, config.py)
- Ed25519 signing infrastructure
- FastAPI server implementation
- Modal deployment support
- Docker configuration
- Workers KV binding and KV-based analytics
- TypeScript Worker implementation
- Write operations (PUT/DELETE) - vault is read-only

**Added:**
- CSV-based storage (`worker/secrets/` directory)
- Python Cloudflare Worker (`worker/index.py`, `WorkerEntrypoint` class)
- Context isolation via CSV filenames
- `label` and `update_count` secret fields
- Read-only deployment workflow (edit CSV + `npm run deploy`)

**Simplified:**
- Single authentication method (bearer token via `wrangler secret`)
- Read-only API surface (get, list; PUT/DELETE return 405)
- Deployment: `npm run deploy` bundles Worker + CSVs via Text rules, no KV setup
