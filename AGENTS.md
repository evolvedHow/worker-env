# worker-env

Ultra-lightweight CSV-based secret distribution on Cloudflare Workers (Python).
Read-only vault optimized for simplicity: edit CSV files, deploy via `wrangler`,
serve secrets programmatically. No KV, no database, no writes.

## Layout

- `wrangler.toml` (repo root) — Cloudflare config. `main = "worker/index.py"`,
  Text-rule glob `secrets/*.csv` bundles the CSVs. No `[vars]` token
  placeholder (it would collide with the encrypted secret on deploy).
- `package.json` (repo root) — npm scripts: `npm run deploy`, `npm run dev`.
  Keep npm files at the root: `wrangler` resolves `main` relative to CWD, and
  bundling `node_modules` from inside `worker/` would blow the 3MB free-tier
  bundle limit.
- `worker/index.py` — Cloudflare Python Worker (`class Default(WorkerEntrypoint)`).
  Loads CSV files from the VFS via `Path(__file__).parent / "secrets"`,
  serves the HTTP API with bearer token auth.
- `worker/secrets/` — CSV files organized by context (git-ignored except
  `.gitkeep`). Rules globs resolve relative to the entry file's directory, so
  CSVs MUST live here, not at the repo root.
  - `secrets.csv` (default context)
  - `{context}.secrets.csv` (named contexts: homelab, finance, commerce, etc.)
- `src/worker_env/` — Python client library and CLI:
  - `client.py` — HTTP client for programmatic access
  - `cli.py` — Command-line tool for querying secrets
  - `models.py` — Pydantic models (Secret, SecretList, SecretValue)
  - `errors.py` — Exception types (VaultError, SecretNotFoundError, etc.)

## Commands

```bash
uv run ruff format src tests examples worker   # format Python code
uv run ruff check src tests examples worker    # lint Python code
uv run mypy src                                # type-check the library
uv run pytest                                  # run tests
uv run vault --help                            # test CLI
npm run dev                                    # test Worker locally (repo root)
npm run deploy                                 # deploy to Cloudflare (repo root)
```

## Rules

- **Schema**: `{context, key, value, label, update_count, updated_at}`.
  Context derived from CSV filename (e.g., `homelab.secrets.csv` → "homelab").
- **Context naming**: name a context after its app — the repo/product name,
  lowercase and joined (e.g., `yuktiAI` → `yuktiai.secrets.csv`). Callers pass
  that name as the context on every request.
- **Default fallback**: `GET /secrets/{context}/{key}` serves the default
  (`secrets.csv`) value when the named context omits the key; a named-context
  value always wins when both define it.
- **CSV format**: `key,value,label,update_count,updated_at` (no context column).
- **Storage**: CSVs in `worker/secrets/`. The default context is cached at cold
  start; each named context is cached on first access. Production needs redeploy
  after edits; `wrangler dev` restarts the isolate so edits are picked up.
- **Read-only**: No PUT/DELETE operations (worker returns 405). Update secrets
  by editing CSV + redeploy.
- **Authentication**: Bearer token in `Authorization` header. Production token
  set via `npx wrangler secret put VAULT_BEARER_TOKEN`; local token via
  `.dev.vars` at the repo root (git-ignored). Vault fails closed (401) while
  the token is unset.
- **Routes**: `GET /health` (unauthenticated), `GET /secrets[?context=...]`,
  `GET /secrets/{context}/{key}` (falls back to the default context),
  `GET /secrets/{key}` (default context).
- **Telemetry**: Cloudflare-native observability (no custom KV analytics).
- **Secrets storage**: Actual secrets in `~/docker/stack/secrets/`, never
  commit. All `worker/secrets/*.csv` are git-ignored (`.gitkeep` tracked).
- **Package manager**: `uv` for Python, `npm` for Worker deployment.
- **Deployment**: `npm run deploy` from the repo root uploads Worker + CSVs.
