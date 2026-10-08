FROM ghcr.io/astral-sh/uv:0.10.7 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('WORKER_ENV_PORT', '8000'), timeout=4)"]

CMD ["sh", "-c", "exec uv run --no-sync uvicorn worker_env.api:create_app --factory --host 127.0.0.1 --port \"${WORKER_ENV_PORT:-8000}\""]
