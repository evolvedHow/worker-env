"""Modal serverless deployment for the worker-env API.

Deploy with:

    uv run --extra modal modal deploy modal_app.py
"""

import modal
from fastapi import FastAPI

from worker_env.api import create_app

IMAGE = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject("pyproject.toml")
    .add_local_python_source("worker_env")
)

app = modal.App("worker-env")


@app.function(image=IMAGE)
@modal.asgi_app()
def serve() -> FastAPI:
    """Expose the signed context API as a Modal web endpoint.

    The function builds the FastAPI application from the ambient
    environment, so `WORKER_ENV_MASTER_PUBLIC_KEYS` and friends must be
    provided through Modal environment variables or secrets.

    Returns:
        The ASGI application served by Modal.
    """
    return create_app()
