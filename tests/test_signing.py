"""Unit tests for Ed25519 signing and verification helpers."""

import time

import pytest

from worker_env.signing import (
    MAX_CLOCK_SKEW_SECONDS,
    canonical_payload,
    current_timestamp,
    generate_keypair,
    sign_request,
    verify_request,
)

NOW = 1_700_000_000


def test_sign_verify_roundtrip() -> None:
    """A signature produced by one key verifies with its public counterpart."""
    pair = generate_keypair()
    signature = sign_request(pair.private_key, NOW, "GET", "/contexts", b"")
    assert verify_request(
        (pair.public_key,),
        signature,
        str(NOW),
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )


def test_verify_rejects_wrong_key() -> None:
    """A signature from an untrusted key does not verify."""
    signer = generate_keypair()
    stranger = generate_keypair()
    signature = sign_request(signer.private_key, NOW, "POST", "/contexts", b"{}")
    assert not verify_request(
        (stranger.public_key,),
        signature,
        str(NOW),
        method="POST",
        path_with_query="/contexts",
        body=b"{}",
        now=NOW,
    )


def test_verify_rejects_tampered_body() -> None:
    """Altering the body invalidates the signature."""
    pair = generate_keypair()
    signature = sign_request(pair.private_key, NOW, "POST", "/contexts", b'{"a":1}')
    assert not verify_request(
        (pair.public_key,),
        signature,
        str(NOW),
        method="POST",
        path_with_query="/contexts",
        body=b'{"a":2}',
        now=NOW,
    )


def test_verify_rejects_stale_timestamp() -> None:
    """Timestamps older than the skew window are rejected."""
    pair = generate_keypair()
    stale = NOW - MAX_CLOCK_SKEW_SECONDS - 1
    signature = sign_request(pair.private_key, stale, "GET", "/contexts", b"")
    assert not verify_request(
        (pair.public_key,),
        signature,
        str(stale),
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )


def test_verify_rejects_future_timestamp() -> None:
    """Timestamps far in the future are rejected."""
    pair = generate_keypair()
    future = NOW + MAX_CLOCK_SKEW_SECONDS + 1
    signature = sign_request(pair.private_key, future, "GET", "/contexts", b"")
    assert not verify_request(
        (pair.public_key,),
        signature,
        str(future),
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )


def test_verify_rejects_malformed_inputs() -> None:
    """Missing headers, bad base64, and non-numeric timestamps fail closed."""
    pair = generate_keypair()
    signature = sign_request(pair.private_key, NOW, "GET", "/contexts", b"")
    assert not verify_request(
        (pair.public_key,),
        None,
        str(NOW),
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )
    assert not verify_request(
        (pair.public_key,),
        signature,
        None,
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )
    assert not verify_request(
        (pair.public_key,),
        "not-base64!!",
        str(NOW),
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )
    assert not verify_request(
        (pair.public_key,),
        signature,
        "yesterday",
        method="GET",
        path_with_query="/contexts",
        body=b"",
        now=NOW,
    )


def test_sign_request_rejects_bad_key_length() -> None:
    """Signing with a non-32-byte key raises a clear error."""
    with pytest.raises(ValueError, match="32 raw bytes"):
        sign_request(b"short", NOW, "GET", "/contexts", b"")


def test_canonical_payload_binds_all_fields() -> None:
    """Changing method, path, or body changes the canonical bytes."""
    baseline = canonical_payload(NOW, "GET", "/contexts", b"")
    assert baseline != canonical_payload(NOW, "POST", "/contexts", b"")
    assert baseline != canonical_payload(NOW, "GET", "/contexts?namespace=app", b"")
    assert baseline != canonical_payload(NOW, "GET", "/contexts", b"x")
    assert baseline != canonical_payload(NOW + 1, "GET", "/contexts", b"")


def test_current_timestamp_is_recent() -> None:
    """The default timestamp reflects the current clock."""
    assert abs(current_timestamp() - int(time.time())) <= 2
