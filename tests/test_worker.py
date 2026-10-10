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
    ) -> None:
        self.url = url
        self.method = method
        self.headers = FakeHeaders(headers or {})


def _load_worker(monkeypatch: pytest.MonkeyPatch, secrets_dir: Path) -> types.ModuleType:
    """Load ``worker/index.py`` with a stubbed SDK and a temporary secrets dir."""
    fake_workers = types.ModuleType("workers")
    fake_workers.Response = FakeResponse
    fake_workers.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
    monkeypatch.setitem(sys.modules, "workers", fake_workers)

    spec = importlib.util.spec_from_file_location("worker_index", WORKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SECRETS_DIR = secrets_dir
    return module


def _call(
    module: types.ModuleType, request: FakeRequest, token: str | None = TOKEN
) -> FakeResponse:
    """Invoke the Worker fetch handler with a configured bearer token."""
    entry = module.Default()
    entry.env = types.SimpleNamespace(VAULT_BEARER_TOKEN=token)
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
