"""Ed25519 request signing shared by the API server and the vault client."""

import base64
import binascii
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SIGNATURE_HEADER = "X-Worker-Env-Signature"
TIMESTAMP_HEADER = "X-Worker-Env-Timestamp"
CANONICAL_SCHEME = "worker-env/v1"
MAX_CLOCK_SKEW_SECONDS = 300
KEY_LENGTH_BYTES = 32
SIGNATURE_LENGTH_BYTES = 64


@dataclass(frozen=True, slots=True)
class KeyPair:
    """A master Ed25519 key pair in raw wire format."""

    public_key: bytes
    private_key: bytes


def b64_encode(data: bytes) -> str:
    """Encode raw bytes as standard base64 text.

    Args:
        data: Raw bytes to encode.

    Returns:
        ASCII base64 text.
    """
    return base64.b64encode(data).decode("ascii")


def b64_decode(text: str) -> bytes:
    """Decode standard base64 text into raw bytes.

    Args:
        text: Base64 text produced by :func:`b64_encode`.

    Returns:
        The decoded raw bytes.

    Raises:
        ValueError: If ``text`` is not valid base64.
    """
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError(f"invalid base64 input: {exc}") from exc


def current_timestamp() -> int:
    """Return the current Unix time in whole seconds.

    Returns:
        The current timestamp used when signing outbound requests.
    """
    return int(time.time())


def generate_keypair() -> KeyPair:
    """Generate a fresh master Ed25519 key pair.

    Returns:
        A key pair of raw 32-byte public and private keys.
    """
    private_key = Ed25519PrivateKey.generate()
    return KeyPair(
        public_key=private_key.public_key().public_bytes_raw(),
        private_key=private_key.private_bytes_raw(),
    )


def canonical_payload(timestamp: int, method: str, path_with_query: str, body: bytes) -> bytes:
    """Build the byte string that both the client and server sign.

    The payload binds the scheme, timestamp, HTTP method, request path
    (including any query string) and the SHA-256 digest of the raw body,
    so a signature cannot be replayed against a different request.

    Args:
        timestamp: Unix timestamp in seconds used for the signature.
        method: HTTP method, for example ``GET``.
        path_with_query: Request path including any query string.
        body: Raw request body bytes; empty for bodyless requests.

    Returns:
        The canonical UTF-8 byte string.
    """
    body_digest = hashlib.sha256(body).hexdigest()
    parts = (CANONICAL_SCHEME, str(timestamp), method.upper(), path_with_query, body_digest)
    return "\n".join(parts).encode("utf-8")


def sign_request(
    private_key: bytes,
    timestamp: int,
    method: str,
    path_with_query: str,
    body: bytes,
) -> str:
    """Sign a request with a raw Ed25519 private key.

    Args:
        private_key: Raw 32-byte Ed25519 private key.
        timestamp: Unix timestamp in seconds included in the signature.
        method: HTTP method of the request.
        path_with_query: Request path including any query string.
        body: Raw request body bytes.

    Returns:
        The base64-encoded signature to send in the signature header.

    Raises:
        ValueError: If ``private_key`` is not 32 raw bytes.
    """
    if len(private_key) != KEY_LENGTH_BYTES:
        raise ValueError(f"private key must be {KEY_LENGTH_BYTES} raw bytes")
    signer = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = signer.sign(canonical_payload(timestamp, method, path_with_query, body))
    return b64_encode(signature)


def _fresh_timestamp(timestamp: str | None, now: float | None) -> int | None:
    """Parse a timestamp header and check it against the allowed skew window.

    Args:
        timestamp: Timestamp header value from the request, if present.
        now: Current Unix time override, used for deterministic tests.

    Returns:
        The signed Unix timestamp, or ``None`` when absent, malformed, or stale.
    """
    if not timestamp:
        return None
    try:
        signed_at = int(timestamp)
    except ValueError:
        return None
    current = time.time() if now is None else now
    if abs(current - signed_at) > MAX_CLOCK_SKEW_SECONDS:
        return None
    return signed_at


def verify_request(
    public_keys: Sequence[bytes],
    signature: str | None,
    timestamp: str | None,
    *,
    method: str,
    path_with_query: str,
    body: bytes,
    now: float | None = None,
) -> bool:
    """Verify a signed request against any trusted public key.

    Args:
        public_keys: Trusted raw 32-byte Ed25519 public keys.
        signature: Base64 signature from the request headers, if present.
        timestamp: Timestamp header value from the request, if present.
        method: HTTP method of the incoming request.
        path_with_query: Request path including any query string.
        body: Raw request body bytes.
        now: Current Unix time override, used for deterministic tests.

    Returns:
        ``True`` if the timestamp is fresh and any key verifies the
        signature, otherwise ``False``.
    """
    if not signature:
        return False
    signed_at = _fresh_timestamp(timestamp, now)
    if signed_at is None:
        return False
    try:
        raw_signature = b64_decode(signature)
    except ValueError:
        return False
    if len(raw_signature) != SIGNATURE_LENGTH_BYTES:
        return False
    payload = canonical_payload(signed_at, method, path_with_query, body)
    for key in public_keys:
        if len(key) != KEY_LENGTH_BYTES:
            continue
        verifier = Ed25519PublicKey.from_public_bytes(key)
        try:
            verifier.verify(raw_signature, payload)
        except InvalidSignature:
            continue
        return True
    return False
