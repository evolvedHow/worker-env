"""Tests for the Cloudflare Worker routing, auth, and CSV loading.

The Worker entry point imports the Cloudflare ``workers`` SDK, which is only
provided by the Pyodide runtime. These tests inject a minimal stub so the
pure request-handling logic can run under a normal CPython interpreter.
"""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

WORKER_PATH = Path(__file__).resolve().parent.parent / "worker" / "index.py"
TOKEN = "test-token"
AUTH_HEADER = {"Authorization": f"Bearer {TOKEN}"}
CSV_HEADER = "key,value,label,update_count,updated_at"


class FakeResponse:
    """Minimal stand-in for ``workers.Response``."""

    def __init__(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}

    def json(self) -> Any:
        """Decode the response body as JSON."""
        return json.loads(self.body)


class FakeHeaders:
    """Minimal stand-in for the request ``Headers`` object."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = {key.lower(): value for key, value in values.items()}

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return a header value case-insensitively."""
        return self._values.get(name.lower(), default)


class FakeRequest:
    """Minimal stand-in for an incoming HTTP request."""

    def __init__(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.url = url
        self.method = method
        self.headers = FakeHeaders(headers or {})
        self._body = body

    def bytes(self) -> bytes | None:
        """Return the body bytes set on the request."""
        return self._body


async def _stub_fetch(*_args: Any, **_kwargs: Any) -> Any:
    """Outbound fetch in tests must be replaced per-test; fail loudly otherwise."""
    raise NotImplementedError("test must stub the workers fetch")


class FakeUpstream:
    """Minimal stand-in for an outbound fetch response."""

    def __init__(
        self,
        status: int,
        body: object = "",
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type}

    async def text(self) -> str:
        """Return the body as text for error details."""
        return str(self.body)


def _load_worker(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> types.ModuleType:
    """Load ``worker/index.py`` with a stubbed SDK and a temporary secrets dir."""
    worker_dir = str(WORKER_PATH.parent)
    monkeypatch.syspath_prepend(worker_dir)

    fake_workers = types.ModuleType("workers")
    fake_workers.Response = FakeResponse
    fake_workers.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
    fake_workers.fetch = _stub_fetch
    monkeypatch.setitem(sys.modules, "workers", fake_workers)

    spec = importlib.util.spec_from_file_location("worker_index", WORKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SECRETS_DIR = secrets_dir
    return module


def _env(token: str | None, **extra: Any) -> types.SimpleNamespace:
    """Build a Worker env with the gateway vars and the given token."""
    return types.SimpleNamespace(
        VAULT_BEARER_TOKEN=token,
        ALLOWED_ORIGINS="*",
        DEFAULT_PROVIDER="anthropic",
        VAULT_CONTEXT="yuktiai",
        EXTRA_PROVIDERS="[]",
        **extra,
    )


def _call(
    module: types.ModuleType,
    request: FakeRequest,
    token: str | None = TOKEN,
    env: types.SimpleNamespace | None = None,
) -> FakeResponse:
    """Invoke the Worker fetch handler with a configured bearer token."""
    entry = module.Default()
    entry.env = env if env is not None else _env(token)
    return asyncio.run(entry.fetch(request))


def _write(path: Path, rows: list[str]) -> None:
    """Write CSV rows to a file."""
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Build a Worker module backed by a temporary two-context vault."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    _write(
        secrets_dir / "secrets.csv",
        [
            CSV_HEADER,
            "example-key,default-value,Default example,1,2026-01-01T00:00:00Z",
        ],
    )
    _write(
        secrets_dir / "homelab.secrets.csv",
        [
            CSV_HEADER,
            "github-token,ghp_secret,GitHub token,2,2026-01-02T00:00:00Z",
        ],
    )
    return _load_worker(monkeypatch, secrets_dir)


def test_health_is_unauthenticated(worker: types.ModuleType) -> None:
    """Health responds without a token."""
    response = _call(worker, FakeRequest("http://vault/health"), token=None)
    assert response.status == 200
    assert response.json()["status"] == "ok"


def test_missing_token_fails_closed(worker: types.ModuleType) -> None:
    """An unset token rejects every data request."""
    response = _call(worker, FakeRequest("http://vault/secrets"), token="")
    assert response.status == 401


def test_wrong_token_is_unauthorized(worker: types.ModuleType) -> None:
    """A mismatched bearer token is rejected."""
    request = FakeRequest("http://vault/secrets", headers={"Authorization": "Bearer nope"})
    assert _call(worker, request).status == 401


def test_get_named_context(worker: types.ModuleType) -> None:
    """A context/key lookup returns the stored record."""
    request = FakeRequest("http://vault/secrets/homelab/github-token", headers=AUTH_HEADER)
    response = _call(worker, request)
    assert response.status == 200
    assert response.json()["value"] == "ghp_secret"
    assert response.json()["context"] == "homelab"


def test_get_default_context(worker: types.ModuleType) -> None:
    """A bare key lookup resolves to the default context."""
    request = FakeRequest("http://vault/secrets/example-key", headers=AUTH_HEADER)
    response = _call(worker, request)
    assert response.status == 200
    assert response.json()["context"] == "default"


def test_list_and_context_filter(worker: types.ModuleType) -> None:
    """Listing returns everything, and the context filter narrows it."""
    all_response = _call(worker, FakeRequest("http://vault/secrets", headers=AUTH_HEADER))
    assert all_response.json()["count"] == 2

    filtered = _call(
        worker,
        FakeRequest("http://vault/secrets?context=homelab", headers=AUTH_HEADER),
    )
    assert filtered.json()["count"] == 1
    assert filtered.json()["secrets"][0]["context"] == "homelab"


def test_missing_secret_returns_404(worker: types.ModuleType) -> None:
    """An unknown key yields 404."""
    request = FakeRequest("http://vault/secrets/homelab/missing", headers=AUTH_HEADER)
    assert _call(worker, request).status == 404


@pytest.fixture
def overlapped_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A Worker module where the default context shares a key with a named one."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    _write(
        secrets_dir / "secrets.csv",
        [
            CSV_HEADER,
            "example-key,default-value,Default example,1,2026-01-01T00:00:00Z",
            "shared-key,from-default,Shared default,1,2026-01-01T00:00:00Z",
        ],
    )
    _write(
        secrets_dir / "yuktiai.secrets.csv",
        [CSV_HEADER, "shared-key,from-context,Shared context,1,2026-01-01T00:00:00Z"],
    )
    return _load_worker(monkeypatch, secrets_dir)


def test_named_context_precedence(overlapped_worker: types.ModuleType) -> None:
    """A key defined in the named context is not overridden by the default."""
    request = FakeRequest("http://vault/secrets/yuktiai/shared-key", headers=AUTH_HEADER)
    response = _call(overlapped_worker, request)
    assert response.status == 200
    assert response.json()["value"] == "from-context"
    assert response.json()["context"] == "yuktiai"


def test_named_context_falls_back_to_default(overlapped_worker: types.ModuleType) -> None:
    """A key absent from the named context is served from the default context."""
    request = FakeRequest("http://vault/secrets/yuktiai/example-key", headers=AUTH_HEADER)
    response = _call(overlapped_worker, request)
    assert response.status == 200
    assert response.json()["value"] == "default-value"
    assert response.json()["context"] == "default"


def test_missing_in_both_contexts_returns_404(overlapped_worker: types.ModuleType) -> None:
    """A key absent from both the named and default contexts yields 404."""
    request = FakeRequest("http://vault/secrets/yuktiai/missing", headers=AUTH_HEADER)
    assert _call(overlapped_worker, request).status == 404


def test_unknown_route_returns_404(worker: types.ModuleType) -> None:
    """A non-secrets path yields 404."""
    assert _call(worker, FakeRequest("http://vault/nope", headers=AUTH_HEADER)).status == 404


def test_writes_are_rejected(worker: types.ModuleType) -> None:
    """PUT is refused because the vault is read-only."""
    request = FakeRequest("http://vault/secrets/homelab/key", method="PUT", headers=AUTH_HEADER)
    assert _call(worker, request).status == 405


def test_malformed_update_count_is_normalized(worker: types.ModuleType) -> None:
    """A non-numeric update_count must not crash the whole load."""
    _write(
        worker.SECRETS_DIR / "broken.secrets.csv",
        [CSV_HEADER, "bad-key,value,label,not-a-number,2026-01-01T00:00:00Z"],
    )
    response = _call(worker, FakeRequest("http://vault/secrets", headers=AUTH_HEADER))
    assert response.status == 200
    contexts = {secret["context"] for secret in response.json()["secrets"]}
    assert {"default", "homelab"} <= contexts


def test_unreadable_context_is_skipped(worker: types.ModuleType) -> None:
    """An unreadable context file is skipped instead of failing every request."""
    (worker.SECRETS_DIR / "weird.secrets.csv").mkdir()
    response = _call(worker, FakeRequest("http://vault/secrets", headers=AUTH_HEADER))
    assert response.status == 200
    assert response.json()["count"] == 2


# ── Gateway routes ──────────────────────────────────────────────────────────


@pytest.fixture
def gateway_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A Worker whose vault carries a passcode plus Groq/Anthropic provider keys."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    _write(
        secrets_dir / "secrets.csv",
        [
            CSV_HEADER,
            "ACCESS_PASSCODE,letmein,Shared passcode,0,2026-01-01T00:00:00Z",
            "KEY_ANTHROPIC,sk-ant-test,Shared Anthropic key,0,2026-01-01T00:00:00Z",
        ],
    )
    _write(
        secrets_dir / "yuktiai.secrets.csv",
        [CSV_HEADER, "KEY_GROQ,gsk_groqtest,Groq key,0,2026-01-01T00:00:00Z"],
    )
    return _load_worker(monkeypatch, secrets_dir)


def test_providers_is_public(gateway_worker: types.ModuleType) -> None:
    """The provider catalogue needs no token and carries CORS headers."""
    response = _call(gateway_worker, FakeRequest("http://vault/providers"), token=None)
    assert response.status == 200
    body = response.json()
    assert body["requiresPasscode"] is True
    assert body["defaultProvider"] == "anthropic"
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_providers_report_server_keys(gateway_worker: types.ModuleType) -> None:
    """Keyed profiles report hasServerKey; keys never appear in the payload."""
    response = _call(gateway_worker, FakeRequest("http://vault/providers"), token=None)
    providers = {p["id"]: p for p in response.json()["providers"]}

    assert providers["groq"]["hasServerKey"] is True
    assert providers["anthropic"]["hasServerKey"] is True
    assert providers["openai"]["hasServerKey"] is False
    assert providers["groq"]["baseUrl"] == "https://api.groq.com/openai/v1"
    canned = json.dumps(response.json())
    assert "gsk_groqtest" not in canned
    assert "sk-ant-test" not in canned


def test_chat_rejects_missing_or_wrong_passcode(gateway_worker: types.ModuleType) -> None:
    """A COST gateway refuses paid calls without the right passcode."""
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    req = FakeRequest(
        "http://vault/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=body,
    )
    assert _call(gateway_worker, req, token=None).status == 401

    req = FakeRequest(
        "http://vault/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "bogus"},
        body=body,
    )
    assert _call(gateway_worker, req, token=None).status == 401


def test_chat_proxies_with_server_key(gateway_worker: types.ModuleType) -> None:
    """A correct passcode lets the gateway forward with the vault key injected."""
    calls: dict[str, Any] = {}

    async def fake_fetch(url: str, **kwargs: Any) -> FakeUpstream:
        calls["url"] = url
        calls["headers"] = kwargs["headers"]
        calls["body"] = kwargs["body"]
        return FakeUpstream(
            200, 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', "text/event-stream"
        )

    gateway_worker.fetch = fake_fetch

    body = json.dumps(
        {
            "provider": "groq",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "max_tokens": 120,
        }
    ).encode()
    req = FakeRequest(
        "http://vault/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "letmein"},
        body=body,
    )
    response = _call(gateway_worker, req, token=None)

    assert response.status == 200
    assert response.body == 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
    assert calls["url"].endswith("/chat/completions")
    assert calls["headers"]["Authorization"] == "Bearer gsk_groqtest"
    sent = json.loads(calls["body"])
    assert sent["model"] == "groq/compound-mini"
    assert "provider" not in sent


def test_chat_unknown_provider_with_correct_passcode(gateway_worker: types.ModuleType) -> None:
    """An unknown provider yields a 400 with the known list."""
    body = json.dumps({"provider": "nope", "messages": []}).encode()
    req = FakeRequest(
        "http://vault/v1/chat/completions",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "letmein"},
        body=body,
    )
    response = _call(gateway_worker, req, token=None)
    assert response.status == 400
    assert "groq" in response.json()["detail"]


def test_verify_links_requires_passcode(gateway_worker: types.ModuleType) -> None:
    """Link probing is paid work and gated like chat."""
    req = FakeRequest(
        "http://vault/verify-links",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "bogus"},
        body=json.dumps({"urls": ["https://example.com"]}).encode(),
    )
    response = _call(gateway_worker, req, token=None)
    assert response.status == 401


def test_verify_links_classes_private_host(gateway_worker: types.ModuleType) -> None:
    """Private/loopback URLs are refused without any outbound request."""
    req = FakeRequest(
        "http://vault/verify-links",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "letmein"},
        body=json.dumps({"urls": ["http://10.0.0.5/admin"]}).encode(),
    )
    response = _call(gateway_worker, req, token=None)
    assert response.status == 200
    (result,) = response.json()["results"]
    assert result["verdict"] == "blocked"
    assert "private" in result["reason"]


def test_verify_links_uses_verdicts_from_stub(gateway_worker: types.ModuleType) -> None:
    """Verdicts come from actually probing — stubbed here with status codes."""

    async def fake_fetch(url: str, **kwargs: Any) -> FakeUpstream:
        del kwargs
        status = 404 if "missing" in url else 200
        return FakeUpstream(status)

    verify_module = importlib.import_module("verify_links")
    verify_module.fetch = fake_fetch

    req = FakeRequest(
        "http://vault/verify-links",
        method="POST",
        headers={"Content-Type": "application/json", "X-Yukti-Passcode": "letmein"},
        body=json.dumps(
            {"urls": ["https://example.com/missing", "https://example.com/live"]}
        ).encode(),
    )
    response = _call(gateway_worker, req, token=None)
    assert response.status == 200
    by_url = {r["url"]: r for r in response.json()["results"]}
    assert by_url["https://example.com/missing"]["verdict"] == "missing"
    assert by_url["https://example.com/live"]["verdict"] == "live"


def test_options_preflight_is_unauthenticated(gateway_worker: types.ModuleType) -> None:
    """Browsers preflight the gateway routes without a token."""
    response = _call(
        gateway_worker, FakeRequest("http://vault/providers", method="OPTIONS"), token=None
    )
    assert response.status == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"
