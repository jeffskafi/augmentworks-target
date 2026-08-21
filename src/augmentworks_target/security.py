"""HMAC authentication for AugmentWorks target requests.

Signatures bind the raw request body and the exact path (including its query
string).  A timestamp limits replay lifetime, while a nonce store prevents a
valid request from being accepted twice inside that lifetime.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as secrets_module
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

KEY_ID_HEADER = "AW-Key-Id"
TIMESTAMP_HEADER = "AW-Timestamp"
NONCE_HEADER = "AW-Nonce"
SIGNATURE_HEADER = "AW-Signature"

Secret: TypeAlias = str | bytes
SecretResolver: TypeAlias = Mapping[str, Secret] | Callable[[str], Secret | None]


class AuthenticationError(Exception):
    """A safe authentication failure that may cross the HTTP boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NonceStore(Protocol):
    """Atomic replay-protection store.

    Production adapters running more than one process should provide a shared
    implementation (for example, Redis ``SET NX EX``).  ``claim`` must return
    ``False`` when the key-id/nonce pair has already been claimed.
    """

    def claim(
        self,
        *,
        key_id: str,
        nonce: str,
        expires_at: int,
        now: int,
    ) -> bool: ...


class MemoryNonceStore:
    """Process-local nonce store for development and single-process demos.

    This implementation is thread-safe, but it is deliberately not a
    production default for horizontally scaled services: separate processes
    do not share its state.
    """

    def __init__(self) -> None:
        self._expires_at: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def claim(
        self,
        *,
        key_id: str,
        nonce: str,
        expires_at: int,
        now: int,
    ) -> bool:
        candidate = (key_id, nonce)
        with self._lock:
            expired = [key for key, expiry in self._expires_at.items() if expiry < now]
            for key in expired:
                del self._expires_at[key]

            if candidate in self._expires_at:
                return False
            self._expires_at[candidate] = expires_at
            return True


@dataclass(frozen=True, slots=True)
class VerifiedRequest:
    """Identity and freshness metadata from a verified request."""

    key_id: str
    timestamp: int
    nonce: str


_DEFAULT_NONCE_STORE = MemoryNonceStore()


def body_sha256(body: bytes) -> str:
    """Return the lowercase SHA-256 digest used by the canonical request."""

    return hashlib.sha256(body).hexdigest()


def canonical_request(
    *,
    timestamp: int,
    nonce: str,
    method: str,
    path_with_query: str,
    body: bytes,
) -> bytes:
    """Build the exact UTF-8 bytes covered by a request signature."""

    return (
        f"{timestamp}\n{nonce}\n{method.upper()}\n{path_with_query}\n{body_sha256(body)}"
    ).encode()


def sign_request(
    *,
    secret: Secret,
    key_id: str,
    method: str,
    path_with_query: str,
    body: bytes = b"",
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Create the four protocol authentication headers for a request."""

    request_timestamp = int(time.time()) if timestamp is None else timestamp
    request_nonce = secrets_module.token_urlsafe(24) if nonce is None else nonce
    if not key_id or not request_nonce:
        raise ValueError("key_id and nonce must be non-empty")

    digest = hmac.new(
        _secret_bytes(secret),
        canonical_request(
            timestamp=request_timestamp,
            nonce=request_nonce,
            method=method,
            path_with_query=path_with_query,
            body=body,
        ),
        hashlib.sha256,
    ).hexdigest()
    return {
        KEY_ID_HEADER: key_id,
        TIMESTAMP_HEADER: str(request_timestamp),
        NONCE_HEADER: request_nonce,
        SIGNATURE_HEADER: f"v1={digest}",
    }


def verify_request(
    *,
    headers: Mapping[str, str],
    secrets: SecretResolver,
    method: str,
    path_with_query: str,
    body: bytes = b"",
    nonce_store: NonceStore | None = None,
    now: int | None = None,
    max_clock_skew_seconds: int = 300,
) -> VerifiedRequest:
    """Verify a signed request and atomically claim its nonce.

    Signature comparison happens before claiming the nonce, so an attacker
    cannot poison the replay store with unauthenticated values.
    """

    if max_clock_skew_seconds < 1:
        raise ValueError("max_clock_skew_seconds must be positive")

    key_id = _required_header(headers, KEY_ID_HEADER)
    timestamp_text = _required_header(headers, TIMESTAMP_HEADER)
    nonce = _required_header(headers, NONCE_HEADER)
    supplied_signature = _required_header(headers, SIGNATURE_HEADER)

    if len(key_id) > 200 or len(nonce) > 300:
        raise AuthenticationError("invalid_auth_header", "Invalid authentication header")

    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise AuthenticationError(
            "invalid_timestamp", "Authentication timestamp must be Unix seconds"
        ) from exc

    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp) > max_clock_skew_seconds:
        raise AuthenticationError(
            "stale_request", "Authentication timestamp is outside the allowed window"
        )

    secret = _resolve_secret(secrets, key_id)
    if secret is None:
        # Use the same outward category as a bad signature to avoid making key
        # identifiers enumerable.
        raise AuthenticationError("invalid_signature", "Request signature is invalid")

    if not supplied_signature.startswith("v1=") or len(supplied_signature) != 67:
        raise AuthenticationError("invalid_signature", "Request signature is invalid")

    expected = sign_request(
        secret=secret,
        key_id=key_id,
        method=method,
        path_with_query=path_with_query,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
    )[SIGNATURE_HEADER]
    if not hmac.compare_digest(supplied_signature, expected):
        raise AuthenticationError("invalid_signature", "Request signature is invalid")

    store = _DEFAULT_NONCE_STORE if nonce_store is None else nonce_store
    if not store.claim(
        key_id=key_id,
        nonce=nonce,
        expires_at=timestamp + max_clock_skew_seconds,
        now=current_time,
    ):
        raise AuthenticationError("replayed_request", "Request nonce has already been used")

    return VerifiedRequest(key_id=key_id, timestamp=timestamp, nonce=nonce)


def _secret_bytes(secret: Secret) -> bytes:
    return secret if isinstance(secret, bytes) else secret.encode("utf-8")


def _resolve_secret(resolver: SecretResolver, key_id: str) -> Secret | None:
    if callable(resolver):
        return resolver(key_id)
    return resolver.get(key_id)


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None:
        lower_name = name.lower()
        value = next(
            (candidate for key, candidate in headers.items() if key.lower() == lower_name),
            None,
        )
    if value is None or not value.strip():
        raise AuthenticationError(
            "missing_auth_header", f"Missing required authentication header: {name}"
        )
    return value.strip()
