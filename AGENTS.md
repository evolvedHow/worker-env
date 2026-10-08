# worker-env

Minimal secret vault on Cloudflare Workers (TypeScript) with a Python CLI
for management. The frontend/GitHub Pages defaults from `~/codebox/AGENTS.md`
do not apply here — this is a backend-only vault service.

## Layout

- `worker/` — Cloudflare Worker (TypeScript, ~230 lines). The production
  vault serving secrets from KV with bearer token auth.
- `src/worker_env/cli.py` — Python CLI for secret management (get, set,
  delete, list). Communicates with the deployed Worker via HTTP.
- Legacy files (`src/worker_env/{api,signing,store,client,config,models}.py`,
  `tests/`, `modal_app.py`, `Dockerfile`, `docker-compose.yml`) are not
  used in the current simplified architecture.

## Commands

```bash
cd worker && npx tsc --noEmit              # type check Worker
uv run ruff format src/worker_env/cli.py   # format CLI
uv run ruff check src/worker_env/cli.py    # lint CLI
uv run vault --help                        # test CLI
```

## Rules

- **Schema**: `{namespace, key, value, updated_at}`. Namespace provides
  logical isolation (homelab vs. volunteer); key is the secret identifier.
- **Authentication**: Bearer token in `Authorization` header. Token is
  stored in wrangler.toml `[vars]` VAULT_BEARER_TOKEN.
- **KV layout**: `{namespace}:{key}` → JSON(Secret). Analytics (optional):
  `analytics:{namespace}:{key}:{YYYY-MM-DD}` → access count (30-day TTL).
- **No uniqueness enforcement**: Values can duplicate; it's a vault, not
  a constraint engine.
- **Analytics are optional**: Enable with `VAULT_ENABLE_ANALYTICS = "true"`
  in wrangler.toml. Access tracking is async and non-blocking.
- **Secrets storage**: Tokens belong in `~/docker/stack/.env` and
  wrangler.toml `[vars]`; never commit them.
- **Package manager**: `uv` for Python, `npm` for Worker.
