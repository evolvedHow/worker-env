"""CSV-backed secret vault AND single LLM gateway on Cloudflare Workers (Python).

Two roles in one Worker, so every app can point at one canonical deployment:

1. **Secret vault** — serves secrets from per-context CSV files over a read-only
   HTTP API with bearer token authentication. Lookups overlay the default
   context: ``{context}/{key}`` returns the named context's value, or the
   default-context value when the named context does not define the key.

2. **LLM gateway** — holds provider API keys server-side (from the vault CSVs,
   never in the browser) and proxies OpenAI-style chat completions. The static
   frontend picks a provider from ``GET /providers``, sends a passphrase when
   ``ACCESS_PASSCODE`` is set, and streams from ``POST /v1/chat/completions``.
   ``POST /verify-links`` probes cited URLs for the fact-check pass — a browser
   cannot do that because of CORS.

File layout (bundled below the worker entry point):

    worker/secrets/secrets.csv             -> default context
    worker/secrets/{context}.secrets.csv   -> named context

CSV columns: key,value,label,update_count,updated_at

Routes
    OPTIONS *                              CORS preflight
    GET     /health                        liveness (unauthenticated)
    GET     /providers                     provider catalogue, public (no secrets)
    POST    /v1/chat/completions           OpenAI-compatible proxy (passcode-gated)
    POST    /verify-links                  URL probe for fact-checking (passcode-gated)
    GET     /secrets[?context=...]         list secrets (bearer token required)
    GET     /secrets/{context}/{key}       read one secret, default-context fallback
    GET     /secrets/{key}                 read from the default context

Authentication
    /secrets* — ``Authorization: Bearer <VAULT_BEARER_TOKEN>``.
    /v1/* and /verify-links — ``X-Yukti-Passcode`` when ACCESS_PASSCODE is set in
    the vault. The provider keys themselves stay server-side.
    /health and /providers — public.
"""

import csv
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from providers import base_url_var_for, resolve_profiles, secret_name_for
from verify_links import MAX_URLS, verify_links
from workers import Response, WorkerEntrypoint, fetch

VERSION = "1.1.0"
SECRETS_DIR = Path(__file__).parent / "secrets"
DEFAULT_CONTEXT = "default"
DEFAULT_CSV = "secrets.csv"
DEFAULT_VAULT_CONTEXT = "yuktiai"
CONTEXT_SUFFIX = ".secrets.csv"

# Path segment counts for /secrets/... request paths
LIST_PATH_SEGMENTS = 1
SECRET_PATH_SEGMENTS = 3
KEY_PATH_SEGMENTS = 2

# Reject oversized bodies before parsing — debate history is well under this.
MAX_BODY_BYTES = 1_000_000

ALLOWED_REQUEST_HEADERS = (
    "authorization, content-type, x-yukti-provider, x-yukti-passcode, http-referer, x-title"
)

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


def _json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    """Build a JSON HTTP response.

    Args:
        body: JSON-serializable response body.
        status: HTTP status code.
        headers: Extra response headers (typically the CORS headers) to merge in.

    Returns:
        Response with a JSON content type.
    """
    merged = {**(headers or {})}
    merged["Content-Type"] = "application/json"
    return Response(json.dumps(body), status=status, headers=merged)


def _error(status: int, message: str, headers: dict[str, str] | None = None) -> Response:
    """Build a JSON error response.

    Args:
        status: HTTP status code.
        message: User-facing error message.
        headers: Extra response headers to merge in.

    Returns:
        Response with an ``error`` envelope.
    """
    return _json({"detail": message}, status=status, headers=headers)


def _secret_path_segments(path: str) -> list[str]:
    """Split a request path into non-empty segments.

    Args:
        path: Request path such as "/secrets/homelab/key".

    Returns:
        Path segments with empty parts removed.
    """
    return [segment for segment in path.split("/") if segment]


def _cors_headers(env: Any, origin: str | None) -> dict[str, str]:
    """Build CORS headers honouring an origin allowlist.

    Args:
        env: Worker bindings.
        origin: Value of the incoming ``Origin`` request header, if any.

    Returns:
        CORS headers, with no ``Access-Control-Allow-Origin`` when the origin is
        not allowed (browsers then block the response).
    """
    raw = getattr(env, "ALLOWED_ORIGINS", None)
    allow_all = raw is None or str(raw) == "*"
    if allow_all:
        headers: dict[str, str] = {"Access-Control-Allow-Origin": "*"}
    elif origin and origin in {part.strip() for part in str(raw).split(",")}:
        headers = {"Access-Control-Allow-Origin": origin}
    else:
        headers = {}
    headers["Vary"] = "Origin"
    return headers


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings without leaking their contents via length/timing.

    Both sides are hashed first so the comparison length is always identical.

    Args:
        a: First secret.
        b: Second secret.

    Returns:
        True when the strings are identical.
    """
    return hmac.compare_digest(
        hashlib.sha256(a.encode("utf-8")).digest(),
        hashlib.sha256(b.encode("utf-8")).digest(),
    )


def _secret_value(env: Any, name: str) -> str | None:
    """Return a secret's value, overlaying the default context on a named one.

    The named context always wins when it defines the key; otherwise the default
    context is consulted. An absent key or a blank value resolves to None.

    Args:
        env: Worker bindings (for VAULT_CONTEXT).
        name: Secret key, e.g. "KEY_GROQ" or "ACCESS_PASSCODE".

    Returns:
        The secret value, or None when it is configured nowhere (or blank).
    """
    context = str(getattr(env, "VAULT_CONTEXT", "") or DEFAULT_VAULT_CONTEXT)
    record = _context(context).get(name)
    if record is None and context != DEFAULT_CONTEXT:
        record = _context(DEFAULT_CONTEXT).get(name)
    if record is None:
        return None
    value = record.get("value")
    return value if isinstance(value, str) and value else None


class Default(WorkerEntrypoint):
    """Read-only secret vault serving as the single LLM gateway."""

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
        cors = _cors_headers(self.env, request.headers.get("Origin"))

        if method == "OPTIONS":
            return Response(
                None,
                status=204,
                headers={
                    **cors,
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": ALLOWED_REQUEST_HEADERS,
                    "Access-Control-Max-Age": "86400",
                },
            )

        if method == "GET" and parsed_url.path == "/health":
            return _json({"status": "ok", "version": VERSION}, headers=cors)

        if method == "GET" and parsed_url.path == "/providers":
            return await self._handle_providers(cors)

        if method == "POST" and parsed_url.path == "/v1/chat/completions":
            return await self._handle_chat(request, cors)

        if method == "POST" and parsed_url.path == "/verify-links":
            return await self._handle_verify_links(request, cors)

        if segments[:1] != ["secrets"]:
            return _error(404, "Not Found", headers=cors)

        if not self._is_authorized(request):
            return _error(401, "Unauthorized", headers=cors)

        if method != "GET":
            return _error(405, "Vault is read-only; edit CSV files and redeploy", headers=cors)

        if len(segments) == LIST_PATH_SEGMENTS:
            return self._list_secrets(parsed_url.query, cors)

        if len(segments) == SECRET_PATH_SEGMENTS:
            return self._get_secret(segments[1], segments[2], cors)

        if len(segments) == KEY_PATH_SEGMENTS:
            return self._get_secret(DEFAULT_CONTEXT, segments[1], cors)

        return _error(404, "Not Found", headers=cors)

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

    def _get_secret(self, context: str, key: str, cors: dict[str, str]) -> Response:
        """Return a single secret record, falling back to the default context.

        The default (blank) context overlays every named context: when the key
        is absent from the requested context it is read from the default context
        if defined there. A named-context value always wins over the default.

        Args:
            context: Context name (default context when "default").
            key: Secret key within the context.
            cors: CORS response headers.

        Returns:
            The secret record, or 404 when it is defined in neither context.
        """
        secret = _context(context).get(key)
        if secret is None and context != DEFAULT_CONTEXT:
            secret = _context(DEFAULT_CONTEXT).get(key)
        if secret is None:
            return _error(404, f"Secret '{context}/{key}' not found", headers=cors)
        return _json(secret, headers=cors)

    def _list_secrets(self, query: str, cors: dict[str, str]) -> Response:
        """Return all secret records, optionally filtered by context.

        Args:
            query: Raw query string; supports a single context parameter.
            cors: CORS response headers.

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
        return _json({"secrets": secrets, "count": len(secrets)}, headers=cors)

    # ── Gateway routes ─────────────────────────────────────────────────────────

    async def _handle_providers(self, cors: dict[str, str]) -> Response:
        """Return the provider catalogue with per-profile key availability.

        Public: it exposes ids, labels and model names only — never keys.

        Args:
            cors: CORS response headers.

        Returns:
            Envelope with providers, the default, and whether a passcode is set.
        """
        profiles = resolve_profiles(getattr(self.env, "EXTRA_PROVIDERS", None))
        passcode = _secret_value(self.env, "ACCESS_PASSCODE") is not None
        providers = [
            {
                "id": p.id,
                "label": p.label,
                "baseUrl": self._effective_base_url(p.id, p.base_url),
                "defaultModel": p.default_model,
                "models": p.models,
                # Only ever a boolean — the key itself never leaves the Worker.
                "hasServerKey": _secret_value(self.env, secret_name_for(p.id)) is not None,
            }
            for p in profiles
        ]
        default_provider = str(
            getattr(self.env, "DEFAULT_PROVIDER", "") or (profiles[0].id if profiles else "")
        )
        return _json(
            {
                "defaultProvider": default_provider,
                "requiresPasscode": passcode,
                "providers": providers,
            },
            headers=cors,
        )

    async def _handle_chat(self, request: Any, cors: dict[str, str]) -> Response:
        """Proxy an OpenAI-style chat-completions request, streaming when asked.

        Provider selection: the ``X-Yukti-Provider`` header wins, then a
        ``provider`` field in the body, then the deployment default. The key is
        read from the vault server-side and injected here; it never reaches the
        caller. The upstream SSE body is passed straight through.

        Args:
            request: Incoming HTTP request.
            cors: CORS response headers.

        Returns:
            The upstream response (streamed), or an error envelope.
        """
        denied = await self._guard_paid(request, cors)
        if denied is not None:
            return denied

        raw = await self._read_body(request, cors)
        if isinstance(raw, Response):
            return raw
        body = raw

        provider_id = self._read_provider_selector(
            request, body, getattr(self.env, "DEFAULT_PROVIDER", "")
        )

        profiles = resolve_profiles(getattr(self.env, "EXTRA_PROVIDERS", None))
        profile = next(
            (p for p in profiles if p.id == provider_id), None if provider_id else profiles[0]
        )
        if profile is None:
            known = ", ".join(p.id for p in profiles)
            return _error(
                400, f'Unknown provider "{provider_id}". Known providers: {known}.', headers=cors
            )

        if not body.get("model"):
            body["model"] = profile.default_model

        api_key = _secret_value(self.env, secret_name_for(profile.id))
        if api_key is None:
            return _error(
                f'No API key available for "{profile.id}". Add the '
                f"{secret_name_for(profile.id)} secret to the vault context "
                f"{getattr(self.env, 'VAULT_CONTEXT', '') or DEFAULT_VAULT_CONTEXT}.",
                400,
                headers=cors,
            )

        upstream_url = f"{self._effective_base_url(profile.id, profile.base_url)}/chat/completions"
        upstream = await fetch(
            upstream_url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": request.headers.get("HTTP-Referer") or "https://yuktiai.local",
                "X-Title": request.headers.get("X-Title") or "YuktiAI Debate Arena",
            },
            body=json.dumps(body),
        )

        if upstream.status >= 400 or upstream.status < 200:
            detail = (await self._read_upstream_detail(upstream))[:2000]
            if upstream.status < 400:
                message = f"Upstream unreachable (status {upstream.status}): {detail}"
                status: int = 502
            else:
                message = f"Upstream error {upstream.status}: {detail}"
                status = upstream.status
            return _error(status, message, headers=cors)

        headers = dict(cors)
        headers["Content-Type"] = self._upstream_content_type(upstream)
        headers["Cache-Control"] = "no-store"
        return Response(upstream.body, status=upstream.status, headers=headers)

    async def _handle_verify_links(self, request: Any, cors: dict[str, str]) -> Response:
        """Probe a batch of cited URLs and report what the servers said.

        Browsers cannot fetch arbitrary cross-origin sites (CORS), so this runs
        here. Refuses private/loopback hosts — see the verify_links module.

        Args:
            request: Incoming HTTP request.
            cors: CORS response headers.

        Returns:
            Envelope with per-URL results.
        """
        denied = await self._guard_paid(request, cors)
        if denied is not None:
            return denied

        raw = await self._read_body(request, cors)
        if isinstance(raw, Response):
            return raw
        body = raw

        if not isinstance(body.get("urls"), list):
            return _error('Expected { "urls": [ … ] }.', 400, headers=cors)
        urls = [u for u in body["urls"] if isinstance(u, str)]
        results = await verify_links(urls)
        return _json({"results": results, "maxUrls": MAX_URLS}, headers=cors)

    # ── Guards & helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _read_provider_selector(request: Any, body: dict[str, Any], default: str) -> str:
        """Resolve a provider id: header wins, then a body field, then the default.

        The body ``provider`` field is stripped before the request is forwarded so
        upstream providers never see it.

        Args:
            request: Incoming HTTP request.
            body: Parsed request JSON.
            default: Deployment-configured default provider.

        Returns:
            The resolved provider id.
        """
        header = request.headers.get("X-Yukti-Provider")
        body_provider = body.get("provider") if isinstance(body.get("provider"), str) else ""
        body.pop("provider", None)
        return (header or body_provider or default).strip()

    async def _guard_paid(self, request: Any, cors: dict[str, str]) -> Response | None:
        """Passcode check shared by every endpoint that spends money.

        Args:
            request: Incoming HTTP request.
            cors: CORS response headers.

        Returns:
            An error response when the passcode is missing, else None.
        """
        passcode = _secret_value(self.env, "ACCESS_PASSCODE")
        if passcode is None:
            return None
        supplied = request.headers.get("X-Yukti-Passcode") or ""
        if not _constant_time_equals(supplied, passcode):
            return _error(401, "Invalid or missing passcode.", headers=cors)
        return None

    async def _read_body(self, request: Any, cors: dict[str, str]) -> dict[str, Any] | Response:
        """Read and parse a request body as JSON, bounding its size.

        Args:
            request: Incoming HTTP request.
            cors: CORS response headers.

        Returns:
            The parsed object, or an error Response when unreadable/oversized.
        """
        try:
            raw = await self._body_bytes(request)
            if isinstance(raw, bytes) and len(raw) > MAX_BODY_BYTES:
                return _error(413, "Request body too large.", headers=cors)

            if raw is None:
                json_method = getattr(request, "json", None)
                if json_method is None:
                    return _error("Request body is not valid JSON.", 400, headers=cors)
                parsed = json_method()
                if hasattr(parsed, "__await__"):
                    parsed = await parsed
            else:
                parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError, ValueError):
            return _error("Request body is not valid JSON.", 400, headers=cors)

        if not isinstance(parsed, dict):
            return _error("Request body must be a JSON object.", 400, headers=cors)
        return parsed

    async def _body_bytes(self, request: Any) -> bytes | None:
        """Read the request body as bytes when the transport supports it.

        Args:
            request: Incoming HTTP request.

        Returns:
            Raw body bytes, or None when the transport has no byte reader.
        """
        bytes_method = getattr(request, "bytes", None)
        if bytes_method is None:
            return None
        raw = bytes_method()
        if hasattr(raw, "__await__"):
            return await raw
        return raw

    def _effective_base_url(self, provider_id: str, fallback: str) -> str:
        """Return a profile's base URL, honouring a per-deployment override.

        Args:
            provider_id: Profile id.
            fallback: The profile's built-in base URL.

        Returns:
            The override when set, else the built-in URL, trailing slash removed.
        """
        override = getattr(self.env, base_url_var_for(provider_id), None)
        chosen = override.strip() if isinstance(override, str) and override.strip() else fallback
        return chosen.rstrip("/")

    async def _read_upstream_detail(self, upstream: Any) -> str:
        """Read (bounded) error detail from a failed upstream response.

        Args:
            upstream: The upstream fetch response.

        Returns:
            Upstream error text, or an empty string when unreadable.
        """
        text = getattr(upstream, "text", None)
        if callable(text):
            try:
                result = text()
                if hasattr(result, "__await__"):
                    result = await result
            except (TypeError, ValueError):
                result = None
            if result is not None:
                return str(result)

        body = getattr(upstream, "body", "")
        if isinstance(body, (bytes, str)):
            return str(body)
        return ""

    @staticmethod
    def _upstream_content_type(upstream: Any) -> str:
        """Read the upstream Content-Type header, defaulting to JSON.

        Args:
            upstream: The upstream fetch response.

        Returns:
            The upstream content type, or "application/json".
        """
        headers = getattr(upstream, "headers", None)
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("Content-Type")
            if isinstance(value, str) and value:
                return value
        return "application/json"
