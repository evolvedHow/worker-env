"""FastAPI application exposing signed context-management endpoints."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

import httpx
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from worker_env import __version__
from worker_env.client import VaultClient
from worker_env.config import Settings, load_settings
from worker_env.errors import (
    ContextAlreadyExistsError,
    ContextNotFoundError,
    VaultError,
)
from worker_env.models import (
    SLUG_PATTERN,
    Context,
    ContextCreate,
    ContextList,
    ContextUpdate,
    ErrorDetail,
    HealthStatus,
)
from worker_env.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify_request
from worker_env.store import ContextStore, InMemoryContextStore, VaultContextStore

AUTH_RESPONSE: dict[str, Any] = {
    "description": "Missing, stale, or invalid request signature",
    "model": ErrorDetail,
}
NOT_FOUND_RESPONSE: dict[str, Any] = {
    "description": "Context not found",
    "model": ErrorDetail,
}
CONFLICT_RESPONSE: dict[str, Any] = {
    "description": "Name or value already exists in the namespace",
    "model": ErrorDetail,
}
SERVER_ERROR_RESPONSE: dict[str, Any] = {
    "description": "Vault backend failure",
    "model": ErrorDetail,
}
ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]


def get_store(request: Request) -> ContextStore:
    """Return the context store bound to the running application.

    Args:
        request: Active HTTP request.

    Returns:
        Application-scoped context store.
    """
    return cast("ContextStore", request.app.state.store)


def get_settings(request: Request) -> Settings:
    """Return the settings bound to the running application.

    Args:
        request: Active HTTP request.

    Returns:
        Application-scoped settings.
    """
    return cast("Settings", request.app.state.settings)


def _request_target(request: Request) -> str:
    """Reconstruct the byte-exact request target used for signing.

    Prefers the raw, still-percent-encoded path and query from the ASGI
    scope so the server signs exactly the bytes that were transmitted,
    regardless of any later URL decoding.

    Args:
        request: Active HTTP request.

    Returns:
        The request path including its query string, if any.
    """
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes) and raw_path:
        target = raw_path.decode("latin-1")
    else:
        target = request.url.path
    raw_query = request.scope.get("query_string", b"")
    if isinstance(raw_query, bytes) and raw_query:
        return f"{target}?{raw_query.decode('latin-1')}"
    if isinstance(raw_query, str) and raw_query:
        return f"{target}?{raw_query}"
    return target


async def require_signed_request(request: Request) -> None:
    """Reject requests that lack a valid Ed25519 request signature.

    The signature covers the HTTP method, the path with its query string,
    and the SHA-256 digest of the raw body, and must be fresh within the
    allowed clock-skew window.

    Args:
        request: Active HTTP request.

    Raises:
        HTTPException: 401 if headers are missing, the timestamp is stale,
            or the signature does not verify against any trusted key.
    """
    settings = get_settings(request)
    if not settings.master_public_keys:
        if settings.allow_insecure:
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="request signing is not configured",
        )
    body = await request.body()
    path_text = _request_target(request)
    signature = request.headers.get(SIGNATURE_HEADER)
    timestamp = request.headers.get(TIMESTAMP_HEADER)
    if not verify_request(
        settings.master_public_keys,
        signature,
        timestamp,
        method=request.method,
        path_with_query=path_text,
        body=body,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid request signature",
        )


def handle_not_found(_request: Request, exc: ContextNotFoundError) -> JSONResponse:
    """Translate missing-context errors into HTTP 404 responses.

    Args:
        _request: Active HTTP request.
        exc: Raised domain error.

    Returns:
        A 404 JSON response carrying the error detail.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


def handle_conflict(_request: Request, exc: ContextAlreadyExistsError) -> JSONResponse:
    """Translate uniqueness violations into HTTP 409 responses.

    Args:
        _request: Active HTTP request.
        exc: Raised domain error.

    Returns:
        A 409 JSON response carrying the error detail.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


def handle_vault_error(_request: Request, exc: VaultError) -> JSONResponse:
    """Surface unexpected vault HTTP errors as bad-gateway responses.

    Args:
        _request: Active HTTP request.
        exc: Raised vault client error.

    Returns:
        A 502 JSON response carrying the error detail.
    """
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"vault error: {exc.detail}"},
    )


def handle_unreachable_vault(_request: Request, exc: httpx.HTTPError) -> JSONResponse:
    """Surface vault connectivity failures as bad-gateway responses.

    Args:
        _request: Active HTTP request.
        exc: Raised transport-level HTTP error.

    Returns:
        A 502 JSON response describing the failure.
    """
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"vault unreachable: {exc}"},
    )


def build_router() -> APIRouter:
    """Build the context CRUD router with signature verification applied.

    Returns:
        A router whose every route requires a valid request signature.
    """
    router = APIRouter(
        tags=["contexts"],
        dependencies=[Depends(require_signed_request)],
    )

    @router.post(
        "/contexts",
        status_code=status.HTTP_201_CREATED,
        summary="Create a context",
        description=(
            "Creates a context inside a namespace. Both `name` and `value` "
            "must be unique within the namespace. `usage_count` starts at 0 "
            "and `create_date` is stamped by the server."
        ),
        responses={409: CONFLICT_RESPONSE},
    )
    async def create_context(
        payload: ContextCreate,
        store: Annotated[ContextStore, Depends(get_store)],
    ) -> Context:
        """Create a context from ``payload``.

        Args:
            payload: Validated creation payload.
            store: Application-scoped context store.

        Returns:
            The newly stored context record.
        """
        return await store.create(payload)

    @router.get(
        "/contexts",
        summary="List contexts",
        description=(
            "Lists contexts in stable namespace/name order, optionally "
            "filtered by namespace. Listing never increments `usage_count`."
        ),
    )
    async def list_contexts(
        store: Annotated[ContextStore, Depends(get_store)],
        namespace: Annotated[
            str | None,
            Query(
                min_length=1,
                max_length=128,
                pattern=SLUG_PATTERN,
                description="Optional namespace filter.",
            ),
        ] = None,
    ) -> ContextList:
        """List stored contexts without touching usage counters.

        Args:
            store: Application-scoped context store.
            namespace: Optional namespace filter.

        Returns:
            The matching contexts and their count.
        """
        contexts = await store.list_contexts(namespace)
        return ContextList(contexts=contexts, count=len(contexts))

    @router.get(
        "/contexts/{namespace}/{name}",
        summary="Get a context",
        description=(
            "Fetches one context and increments `usage_count`. Retrieval of "
            "a single context is the only operation that ever increments "
            "the counter."
        ),
        responses={404: NOT_FOUND_RESPONSE},
    )
    async def get_context(
        store: Annotated[ContextStore, Depends(get_store)],
        namespace: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Owning namespace."),
        ],
        name: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Context name."),
        ],
    ) -> Context:
        """Fetch a context and increment its usage counter.

        Args:
            store: Application-scoped context store.
            namespace: Namespace owning the context.
            name: Context name.

        Returns:
            The stored record after the counter increment.
        """
        return await store.get(namespace, name)

    @router.patch(
        "/contexts/{namespace}/{name}",
        summary="Update a context",
        description=(
            "Updates `label` and/or `value`. Names and namespaces are "
            "immutable and rejected when supplied; updates never change "
            "`usage_count` or `create_date`. The value may be updated any "
            "number of times."
        ),
        responses={404: NOT_FOUND_RESPONSE, 409: CONFLICT_RESPONSE},
    )
    async def update_context(
        store: Annotated[ContextStore, Depends(get_store)],
        namespace: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Owning namespace."),
        ],
        name: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Context name."),
        ],
        payload: ContextUpdate,
    ) -> Context:
        """Apply a ``label``/``value`` patch to an existing context.

        Args:
            store: Application-scoped context store.
            namespace: Namespace owning the context.
            name: Context name.
            payload: Validated update payload.

        Returns:
            The updated context record.
        """
        return await store.update(namespace, name, payload)

    @router.delete(
        "/contexts/{namespace}/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete a context",
        description="Deletes a context and its value uniqueness entry.",
        responses={404: NOT_FOUND_RESPONSE},
    )
    async def delete_context(
        store: Annotated[ContextStore, Depends(get_store)],
        namespace: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Owning namespace."),
        ],
        name: Annotated[
            str,
            Path(pattern=SLUG_PATTERN, max_length=128, description="Context name."),
        ],
    ) -> Response:
        """Delete a context.

        Args:
            store: Application-scoped context store.
            namespace: Namespace owning the context.
            name: Context name.

        Returns:
            An empty 204 response.
        """
        await store.delete(namespace, name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _build_store(settings: Settings) -> ContextStore:
    """Instantiate the store backend selected by the settings.

    Args:
        settings: Validated application settings.

    Returns:
        A store ready for use.

    Raises:
        ValueError: If the vault store lacks its URL or signing key.
    """
    if settings.store == "vault":
        if settings.vault_url is None:
            raise ValueError("vault store requires WORKER_ENV_VAULT_URL")
        if settings.master_private_key is None:
            raise ValueError(
                "vault store requires WORKER_ENV_MASTER_PRIVATE_KEY to sign outbound requests"
            )
        return VaultContextStore(VaultClient(settings.vault_url, settings.master_private_key))
    return InMemoryContextStore()


def create_app(
    settings: Settings | None = None,
    store: ContextStore | None = None,
) -> FastAPI:
    """Build the worker-env FastAPI application.

    Args:
        settings: Explicit settings; loaded from the environment when omitted.
        store: Explicit store; built from ``settings`` when omitted.

    Returns:
        A configured application instance.

    Raises:
        ValueError: If no trusted public keys are configured and insecure
            mode is not explicitly enabled.
    """
    resolved = load_settings() if settings is None else settings
    if not resolved.master_public_keys and not resolved.allow_insecure:
        raise ValueError(
            "no trusted signing keys configured: run 'worker-env keygen' and "
            "set WORKER_ENV_MASTER_PUBLIC_KEYS, or set "
            "WORKER_ENV_ALLOW_INSECURE=true for local development only"
        )
    resolved_store = store if store is not None else _build_store(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Close outbound vault connections when the server shuts down.

        Args:
            application: The running application instance.

        Yields:
            Control to the server for the duration of its lifecycle.
        """
        yield
        application_store = cast("ContextStore", application.state.store)
        await application_store.aclose()

    app = FastAPI(
        title="worker-env",
        summary="Signed context vault API",
        description=(
            "Manages namespaced contexts behind Ed25519 request signing. "
            "Only `label` and `value` may change after creation; names are "
            "immutable and `usage_count` increments solely when a single "
            "context is retrieved."
        ),
        version=__version__,
        lifespan=lifespan,
        responses={401: AUTH_RESPONSE},
    )
    app.state.settings = resolved
    app.state.store = resolved_store
    app.add_exception_handler(ContextNotFoundError, cast("ExceptionHandler", handle_not_found))
    app.add_exception_handler(ContextAlreadyExistsError, cast("ExceptionHandler", handle_conflict))
    app.add_exception_handler(VaultError, cast("ExceptionHandler", handle_vault_error))
    app.add_exception_handler(httpx.HTTPError, cast("ExceptionHandler", handle_unreachable_vault))
    app.include_router(build_router())

    @app.get(
        "/health",
        summary="Health probe",
        description=(
            "Unauthenticated liveness probe used by container health checks. "
            "It exposes no context data."
        ),
    )
    async def health() -> HealthStatus:
        """Report service liveness without requiring a signature.

        Returns:
            The health payload carrying the running version.
        """
        return HealthStatus(version=__version__)

    return app
