"""CSV-based secret vault on Cloudflare Workers.

Serves secrets from per-context CSV files over a read-only HTTP API with bearer
token authentication. The default (blank) context is read once at cold start and
cached; named contexts are read lazily on first access and cached for the life
of the isolate.

File layout (bundled below the worker entry point):

    worker/secrets/secrets.csv             -> default context
    worker/secrets/{context}.secrets.csv   -> named context

CSV columns: key,value,label,update_count,updated_at

Lookups are context-overlaid on the default (blank) context: a request for
``{context}/{key}`` returns the value defined in that context, or the
default-context value when the named context does not define the key. Contexts
are named after the app (repo/product name, lowercase, e.g. "yuktiai").
"""

import csv
import hmac
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workers import Response, WorkerEntrypoint

VERSION = "1.0.0"
SECRETS_DIR = Path(__file__).parent / "secrets"
DEFAULT_CONTEXT = "default"
DEFAULT_CSV = "secrets.csv"
CONTEXT_SUFFIX = ".secrets.csv"

# Path segment counts for /secrets/... request paths
LIST_PATH_SEGMENTS = 1
SECRET_PATH_SEGMENTS = 3
KEY_PATH_SEGMENTS = 2

# In-memory cache: {context: {key: secret_record}}. The default (blank) context
# is primed on the first request and kept for the life of the isolate; named
# contexts are read lazily on first access and cached thereafter, so a fallback
# to the default never re-reads its CSV.
_store: dict[str, dict[str, dict[str, Any]]] = {}


def _parse_update_count(raw: Any) -> int:
    """Parse a CSV update_count cell, normalizing invalid values to zero.

    Args:
        raw: Raw cell value from the CSV row.

    Returns:
        Non-negative integer count; zero when the cell is missing or malformed.
    """
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _read_csv(csv_path: Path, context: str) -> dict[str, dict[str, Any]]:
    """Parse one secret CSV file into a key-indexed dictionary.

    Malformed rows (for example a non-numeric update_count) are normalized
    rather than raising, so a single bad cell cannot fail the whole load.

    Args:
        csv_path: Path to the CSV file inside the worker bundle.
        context: Context name derived from the file name.

    Returns:
        Mapping of secret key to its record.
    """
    secrets: dict[str, dict[str, Any]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            secrets[key] = {
                "context": context,
                "key": key,
                "value": row.get("value", ""),
                "label": row.get("label", ""),
                "update_count": _parse_update_count(row.get("update_count")),
                "updated_at": row.get("updated_at", ""),
            }
    return secrets


def _safe_read_csv(csv_path: Path, context: str) -> dict[str, dict[str, Any]]:
    """Read one CSV file, returning an empty mapping when it cannot be parsed.

    Args:
        csv_path: Path to the CSV file inside the worker bundle.
        context: Context name derived from the file name.

    Returns:
        Parsed secrets, or an empty mapping when the file is unreadable.
    """
    try:
        return _read_csv(csv_path, context)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        print(f"worker-env: skipping malformed secrets file {csv_path.name}: {exc}")
        return {}


def _context_path(context: str) -> Path:
    """Return the CSV path that backs a context.

    Args:
        context: Context name (the default context when "default").

    Returns:
        Path to the context's CSV file, which may not exist.
    """
    if context == DEFAULT_CONTEXT:
        return SECRETS_DIR / DEFAULT_CSV
    return SECRETS_DIR / f"{context}{CONTEXT_SUFFIX}"


def _load_context(context: str) -> dict[str, dict[str, Any]]:
    """Read one context from disk and cache it in the in-memory store.

    A context whose file is absent caches as empty, so a missing context is not
    re-stat'd on every request. Runs at most once per context per isolate. A
    malformed or unreadable file is skipped (cached as empty) so the worker
    keeps serving the remaining contexts instead of failing every request.

    Args:
        context: Context name to load.

    Returns:
        The freshly loaded, now-cached secrets for the context.
    """
    csv_path = _context_path(context)
    secrets = _safe_read_csv(csv_path, context) if csv_path.is_file() else {}
    _store[context] = secrets
    return secrets


def _context(context: str) -> dict[str, dict[str, Any]]:
    """Return a context's secrets, reading and caching it on first access.

    Args:
        context: Context name.

    Returns:
        The cached secrets for the context.
    """
    cached = _store.get(context)
    if cached is not None:
        return cached
    return _load_context(context)


def _load_all_contexts() -> None:
    """Cache every on-disk context, priming the default context first.

    Used by the listing route, which needs all contexts. The default (blank)
    context is always loaded; named contexts are discovered from
    ``*.secrets.csv`` and cached if not already present.

    Uses ``os.listdir`` rather than ``pathlib.Path.glob`` because the deployed
    Workers VFS supports the former but silently returns nothing for the latter.
    """
    _context(DEFAULT_CONTEXT)
    if SECRETS_DIR.is_dir():
        for name in sorted(os.listdir(SECRETS_DIR)):  # noqa: PTH208
            if name.endswith(CONTEXT_SUFFIX):
                _context(name.removesuffix(CONTEXT_SUFFIX))


def _json(body: Any, status: int = 200) -> Response:
    """Build a JSON HTTP response.

    Args:
        body: JSON-serializable response body.
        status: HTTP status code.

    Returns:
        Response with a JSON content type.
    """
    return Response(
        json.dumps(body),
        status=status,
        headers={"Content-Type": "application/json"},
    )


def _secret_path_segments(path: str) -> list[str]:
    """Split a request path into non-empty segments.

    Args:
        path: Request path such as "/secrets/homelab/key".

    Returns:
        Path segments with empty parts removed.
    """
    return [segment for segment in path.split("/") if segment]


class Default(WorkerEntrypoint):
    """Read-only secret vault serving CSV-backed context/key pairs."""

    async def fetch(self, request: Any) -> Response:
        """Route an incoming request to the appropriate handler.

        Args:
            request: Incoming HTTP request.

        Returns:
            The HTTP response for the request.
        """
        _context(DEFAULT_CONTEXT)

        parsed_url = urlparse(str(request.url))
        method = str(request.method).upper()
        segments = _secret_path_segments(parsed_url.path)

        if method == "GET" and parsed_url.path == "/health":
            return _json({"status": "ok", "version": VERSION})

        if not self._is_authorized(request):
            return _json({"detail": "Unauthorized"}, 401)

        if segments[:1] != ["secrets"]:
            return _json({"detail": "Not Found"}, 404)

        if method != "GET":
            return _json(
                {"detail": "Vault is read-only; edit CSV files and redeploy"},
                405,
            )

        if len(segments) == LIST_PATH_SEGMENTS:
            return self._list_secrets(parsed_url.query)

        if len(segments) == SECRET_PATH_SEGMENTS:
            return self._get_secret(segments[1], segments[2])

        if len(segments) == KEY_PATH_SEGMENTS:
            return self._get_secret(DEFAULT_CONTEXT, segments[1])

        return _json({"detail": "Not Found"}, 404)

    def _is_authorized(self, request: Any) -> bool:
        """Check the bearer token on an incoming request.

        Fails closed when the token is unset or the header is missing.

        Args:
            request: Incoming HTTP request.

        Returns:
            True when the Authorization header carries the configured token.
        """
        token = getattr(self.env, "VAULT_BEARER_TOKEN", "")
        if not token:
            return False
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return False
        return hmac.compare_digest(str(auth_header), f"Bearer {token}")

    def _get_secret(self, context: str, key: str) -> Response:
        """Return a single secret record, falling back to the default context.

        The default (blank) context overlays every named context: when the key
        is absent from the requested context it is read from the default context
        if defined there. A named-context value always wins over the default.

        Args:
            context: Context name (default context when "default").
            key: Secret key within the context.

        Returns:
            The secret record, or 404 when it is defined in neither context.
        """
        secret = _context(context).get(key)
        if secret is None and context != DEFAULT_CONTEXT:
            secret = _context(DEFAULT_CONTEXT).get(key)
        if secret is None:
            return _json({"detail": f"Secret '{context}/{key}' not found"}, 404)
        return _json(secret)

    def _list_secrets(self, query: str) -> Response:
        """Return all secret records, optionally filtered by context.

        Args:
            query: Raw query string; supports a single context parameter.

        Returns:
            Envelope with the matching secrets and their count.
        """
        _load_all_contexts()
        context_filter = parse_qs(query).get("context", [None])[0]
        secrets: list[dict[str, Any]] = []
        for context, entries in _store.items():
            if context_filter and context != context_filter:
                continue
            secrets.extend(entries.values())
        return _json({"secrets": secrets, "count": len(secrets)})
