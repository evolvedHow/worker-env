"""Tests asserting the OpenAPI schema is fully populated at /docs."""

from fastapi import FastAPI

from conftest import SignedClient
from worker_env.api import build_router


def test_openapi_exposes_every_endpoint(app: FastAPI) -> None:
    """All documented routes appear in the generated schema."""
    schema = app.openapi()
    assert set(schema["paths"]) == {
        "/contexts",
        "/contexts/{namespace}/{name}",
        "/health",
    }
    context_item = schema["paths"]["/contexts/{namespace}/{name}"]
    assert set(context_item) == {"get", "patch", "delete"}
    assert set(schema["paths"]["/contexts"]) == {"get", "post"}


def test_openapi_declares_component_schemas(app: FastAPI) -> None:
    """Every request and response model is published in components."""
    schemas = app.openapi()["components"]["schemas"]
    assert {
        "Context",
        "ContextCreate",
        "ContextList",
        "ContextUpdate",
        "ErrorDetail",
        "HealthStatus",
    } <= set(schemas)


def test_openapi_documents_auth_and_errors(app: FastAPI) -> None:
    """Context routes document 401 responses and carry descriptions."""
    schema = app.openapi()
    created = schema["paths"]["/contexts"]["post"]
    assert created["description"]
    assert "409" in created["responses"]
    assert "401" in created["responses"]
    fetched = schema["paths"]["/contexts/{namespace}/{name}"]["get"]
    assert "404" in fetched["responses"]


def test_docs_and_redoc_render(client: SignedClient) -> None:
    """The interactive documentation endpoints are reachable."""
    assert client.raw.get("/docs").status_code == 200
    assert client.raw.get("/redoc").status_code == 200
    assert client.raw.get("/openapi.json").status_code == 200


def test_router_requires_signature_on_every_context_route() -> None:
    """Each context route carries the signature-verification dependency."""
    router = build_router()
    for route in router.routes:
        names = [dependency.call.__name__ for dependency in route.dependant.dependencies]
        assert "require_signed_request" in names
