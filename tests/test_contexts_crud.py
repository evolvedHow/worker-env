"""API tests for context creation, retrieval, updates, and deletion."""

from conftest import SAMPLE_CONTEXT, SignedClient


def test_create_returns_201_with_computed_fields(client: SignedClient) -> None:
    """Creation stamps server-managed fields and echoes client input."""
    response = client.post("/contexts", json=SAMPLE_CONTEXT)
    assert response.status_code == 201
    body = response.json()
    assert body["namespace"] == "app"
    assert body["name"] == "db-url"
    assert body["label"] == "Primary database URL"
    assert body["value"] == "postgres://localhost:5432/app"
    assert body["usage_count"] == 0
    assert body["create_date"]


def test_create_rejects_duplicate_name(client: SignedClient) -> None:
    """Two contexts in one namespace cannot share a name."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    duplicate = {**SAMPLE_CONTEXT, "value": "postgres://other"}
    response = client.post("/contexts", json=duplicate)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_rejects_duplicate_value(client: SignedClient) -> None:
    """Two contexts in one namespace cannot share a value."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    duplicate = {**SAMPLE_CONTEXT, "name": "replica-url"}
    response = client.post("/contexts", json=duplicate)
    assert response.status_code == 409
    assert "value already used" in response.json()["detail"]


def test_name_and_value_may_repeat_across_namespaces(client: SignedClient) -> None:
    """Uniqueness is scoped to the namespace, not global."""
    other = {**SAMPLE_CONTEXT, "namespace": "staging"}
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    assert client.post("/contexts", json=other).status_code == 201
    listing = client.get("/contexts").json()
    assert listing["count"] == 2


def test_create_validates_inputs(client: SignedClient) -> None:
    """Malformed names, blank fields, and extra keys are rejected."""
    bad_namespace = {**SAMPLE_CONTEXT, "namespace": "not a namespace"}
    assert client.post("/contexts", json=bad_namespace).status_code == 422

    blank_label = {**SAMPLE_CONTEXT, "label": "   "}
    assert client.post("/contexts", json=blank_label).status_code == 422

    blank_value = {**SAMPLE_CONTEXT, "value": ""}
    assert client.post("/contexts", json=blank_value).status_code == 422

    oversized = {**SAMPLE_CONTEXT, "value": "x" * 8193}
    assert client.post("/contexts", json=oversized).status_code == 422

    unexpected = {**SAMPLE_CONTEXT, "usage_count": 9}
    assert client.post("/contexts", json=unexpected).status_code == 422


def test_get_increments_usage_count_only_on_retrieval(
    client: SignedClient,
) -> None:
    """Single-context GETs increment the counter; nothing else does."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201

    listing = client.get("/contexts")
    assert listing.json()["contexts"][0]["usage_count"] == 0

    first = client.get("/contexts/app/db-url")
    assert first.json()["usage_count"] == 1
    second = client.get("/contexts/app/db-url")
    assert second.json()["usage_count"] == 2

    after_list = client.get("/contexts")
    assert after_list.json()["contexts"][0]["usage_count"] == 2

    patched = client.patch("/contexts/app/db-url", json={"label": "renamed"})
    assert patched.json()["usage_count"] == 2
    assert patched.json()["create_date"] == second.json()["create_date"]


def test_list_filters_by_namespace(client: SignedClient) -> None:
    """Listing can be scoped to one namespace and never bumps counters."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    other = {**SAMPLE_CONTEXT, "namespace": "staging"}
    assert client.post("/contexts", json=other).status_code == 201

    scoped = client.get("/contexts?namespace=staging")
    payload = scoped.json()
    assert scoped.status_code == 200
    assert payload["count"] == 1
    assert payload["contexts"][0]["namespace"] == "staging"
    assert payload["contexts"][0]["usage_count"] == 0


def test_update_changes_label_and_value_only(client: SignedClient) -> None:
    """Patch updates mutate label/value while pinned fields stay intact."""
    created = client.post("/contexts", json=SAMPLE_CONTEXT).json()

    response = client.patch(
        "/contexts/app/db-url",
        json={"label": "Replica URL", "value": "postgres://replica/app"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "Replica URL"
    assert body["value"] == "postgres://replica/app"
    assert body["namespace"] == created["namespace"]
    assert body["name"] == created["name"]
    assert body["usage_count"] == created["usage_count"]
    assert body["create_date"] == created["create_date"]


def test_value_can_change_repeatedly_without_usage_cost(
    client: SignedClient,
) -> None:
    """Repeated value rotation never touches usage_count or create_date."""
    created = client.post("/contexts", json=SAMPLE_CONTEXT).json()
    for index in range(3):
        response = client.patch(
            "/contexts/app/db-url",
            json={"value": f"postgres://rotated-{index}/app"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["value"] == f"postgres://rotated-{index}/app"
        assert body["usage_count"] == created["usage_count"]
        assert body["create_date"] == created["create_date"]


def test_update_rejects_immutable_and_server_fields(client: SignedClient) -> None:
    """Names, namespaces, usage counts, and dates cannot be supplied."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    for payload in (
        {"name": "new-name"},
        {"namespace": "other"},
        {"usage_count": 10},
        {"create_date": "2020-01-01T00:00:00Z"},
    ):
        response = client.patch("/contexts/app/db-url", json=payload)
        assert response.status_code == 422


def test_update_requires_at_least_one_field(client: SignedClient) -> None:
    """Empty or null-only patches are rejected."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    assert client.patch("/contexts/app/db-url", json={}).status_code == 422
    assert client.patch("/contexts/app/db-url", json={"label": None}).status_code == 422


def test_update_rejects_value_taken_by_sibling(client: SignedClient) -> None:
    """Rotating a value onto a sibling context's value conflicts."""
    first = {**SAMPLE_CONTEXT, "name": "one", "value": "value-one"}
    second = {**SAMPLE_CONTEXT, "name": "two", "value": "value-two"}
    assert client.post("/contexts", json=first).status_code == 201
    assert client.post("/contexts", json=second).status_code == 201
    response = client.patch("/contexts/app/one", json={"value": "value-two"})
    assert response.status_code == 409


def test_update_can_reuse_own_value(client: SignedClient) -> None:
    """Patching a context with its current value is a harmless no-op."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    response = client.patch(
        "/contexts/app/db-url",
        json={"value": SAMPLE_CONTEXT["value"], "label": "same value, new label"},
    )
    assert response.status_code == 200
    assert response.json()["label"] == "same value, new label"


def test_missing_context_returns_404(client: SignedClient) -> None:
    """Reads, patches, and deletes of unknown keys return 404."""
    assert client.get("/contexts/app/missing").status_code == 404
    assert client.patch("/contexts/app/missing", json={"label": "x"}).status_code == 404
    assert client.delete("/contexts/app/missing").status_code == 404


def test_delete_removes_context_and_frees_keys(client: SignedClient) -> None:
    """Deletion removes the record and releases name and value uniqueness."""
    assert client.post("/contexts", json=SAMPLE_CONTEXT).status_code == 201
    assert client.delete("/contexts/app/db-url").status_code == 204
    assert client.get("/contexts/app/db-url").status_code == 404
    assert client.delete("/contexts/app/db-url").status_code == 404

    recreate = client.post("/contexts", json=SAMPLE_CONTEXT)
    assert recreate.status_code == 201
    assert recreate.json()["usage_count"] == 0


def test_invalid_path_parameters_return_422(client: SignedClient) -> None:
    """Path segments violating the slug pattern are rejected."""
    response = client.get("/contexts/bad%20space/name")
    assert response.status_code == 422
