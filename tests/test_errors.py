"""Tests for exception classes."""

from worker_env.errors import AuthenticationError, SecretNotFoundError, VaultError


def test_vault_error():
    """Test VaultError exception."""
    error = VaultError(500, "Internal server error")
    assert error.status_code == 500
    assert error.detail == "Internal server error"
    assert "500" in str(error)
    assert "Internal server error" in str(error)


def test_secret_not_found_error():
    """Test SecretNotFoundError exception."""
    error = SecretNotFoundError("homelab", "missing-key")
    assert error.status_code == 404
    assert error.context == "homelab"
    assert error.key == "missing-key"
    assert "homelab/missing-key" in error.detail
    assert isinstance(error, VaultError)


def test_secret_not_found_error_default_context():
    """Test SecretNotFoundError for the default context (context=None)."""
    error = SecretNotFoundError(None, "missing-key")
    assert error.status_code == 404
    assert error.context is None
    assert error.key == "missing-key"
    assert "missing-key" in error.detail
    assert isinstance(error, VaultError)


def test_authentication_error():
    """Test AuthenticationError exception."""
    error = AuthenticationError("Invalid token")
    assert error.status_code == 401
    assert error.detail == "Invalid token"
    assert isinstance(error, VaultError)


def test_authentication_error_default_message():
    """Test AuthenticationError with default message."""
    error = AuthenticationError()
    assert error.status_code == 401
    assert "bearer token" in error.detail.lower()
