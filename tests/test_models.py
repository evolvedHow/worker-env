"""Tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from worker_env.models import Secret, SecretList


def test_secret_model():
    """Test Secret model validation."""
    secret = Secret(
        context="homelab",
        key="test-key",
        value="test-value",
        label="Test label",
        update_count=2,
        updated_at="2024-01-01T00:00:00Z",
    )
    assert secret.context == "homelab"
    assert secret.key == "test-key"
    assert secret.value == "test-value"
    assert secret.label == "Test label"
    assert secret.update_count == 2
    assert secret.updated_at == "2024-01-01T00:00:00Z"


def test_secret_model_defaults():
    """Test that label and update_count default to empty/zero."""
    secret = Secret(
        context="homelab",
        key="test-key",
        value="test-value",
        updated_at="2024-01-01T00:00:00Z",
    )
    assert secret.label == ""
    assert secret.update_count == 0


def test_secret_model_frozen():
    """Test that Secret model is immutable."""
    secret = Secret(
        context="homelab",
        key="test-key",
        value="test-value",
        updated_at="2024-01-01T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        secret.value = "new-value"  # type: ignore


def test_secret_model_negative_update_count_rejected():
    """Test that negative update_count values are rejected."""
    with pytest.raises(ValidationError):
        Secret(
            context="homelab",
            key="test-key",
            value="test-value",
            update_count=-1,
            updated_at="2024-01-01T00:00:00Z",
        )


def test_secret_list_model():
    """Test SecretList envelope model."""
    secrets = [
        Secret(
            context="homelab",
            key="key1",
            value="value1",
            updated_at="2024-01-01T00:00:00Z",
        ),
        Secret(
            context="finance",
            key="key2",
            value="value2",
            updated_at="2024-01-01T00:00:00Z",
        ),
    ]
    secret_list = SecretList(secrets=secrets, count=2)
    assert len(secret_list.secrets) == 2
    assert secret_list.count == 2


def test_secret_model_extra_fields_forbidden():
    """Test that extra fields are rejected."""
    with pytest.raises(ValidationError):
        Secret(
            context="homelab",
            key="test-key",
            value="test-value",
            updated_at="2024-01-01T00:00:00Z",
            extra_field="not-allowed",  # type: ignore
        )
