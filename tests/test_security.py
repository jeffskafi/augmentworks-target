from __future__ import annotations

import pytest

from augmentworks_target.security import (
    AuthenticationError,
    MemoryNonceStore,
    canonical_request,
    sign_request,
    verify_request,
)

SECRET = "test-secret-that-is-not-used-outside-tests"


def test_canonical_request_binds_method_path_and_body_digest() -> None:
    canonical = canonical_request(
        timestamp=1_700_000_000,
        nonce="nonce_1",
        method="post",
        path_with_query="/v1/attempts?mode=test",
        body=b'{"ok":true}',
    )

    assert canonical.startswith(b"1700000000\nnonce_1\nPOST\n/v1/attempts?mode=test\n")
    assert canonical.count(b"\n") == 4


def test_valid_signature_verifies() -> None:
    headers = sign_request(
        secret=SECRET,
        key_id="test-key",
        method="POST",
        path_with_query="/v1/attempts",
        body=b"{}",
        timestamp=1_700_000_000,
        nonce="nonce_1",
    )

    verified = verify_request(
        headers=headers,
        secrets={"test-key": SECRET},
        method="POST",
        path_with_query="/v1/attempts",
        body=b"{}",
        now=1_700_000_010,
        nonce_store=MemoryNonceStore(),
    )

    assert verified.key_id == "test-key"
    assert verified.nonce == "nonce_1"


def test_tampered_body_fails_verification() -> None:
    headers = sign_request(
        secret=SECRET,
        key_id="test-key",
        method="POST",
        path_with_query="/v1/attempts",
        body=b'{"amount":49}',
        timestamp=1_700_000_000,
        nonce="nonce_2",
    )

    with pytest.raises(AuthenticationError) as raised:
        verify_request(
            headers=headers,
            secrets={"test-key": SECRET},
            method="POST",
            path_with_query="/v1/attempts",
            body=b'{"amount":99}',
            now=1_700_000_010,
            nonce_store=MemoryNonceStore(),
        )

    assert raised.value.code == "invalid_signature"


def test_stale_signature_is_rejected() -> None:
    headers = sign_request(
        secret=SECRET,
        key_id="test-key",
        method="DELETE",
        path_with_query="/v1/attempts/attempt_1",
        timestamp=1_700_000_000,
        nonce="nonce_3",
    )

    with pytest.raises(AuthenticationError) as raised:
        verify_request(
            headers=headers,
            secrets={"test-key": SECRET},
            method="DELETE",
            path_with_query="/v1/attempts/attempt_1",
            now=1_700_000_301,
            nonce_store=MemoryNonceStore(),
        )

    assert raised.value.code == "stale_request"


def test_nonce_cannot_be_replayed() -> None:
    store = MemoryNonceStore()
    headers = sign_request(
        secret=SECRET,
        key_id="test-key",
        method="DELETE",
        path_with_query="/v1/attempts/attempt_1",
        timestamp=1_700_000_000,
        nonce="nonce_4",
    )
    request = {
        "headers": headers,
        "secrets": {"test-key": SECRET},
        "method": "DELETE",
        "path_with_query": "/v1/attempts/attempt_1",
        "now": 1_700_000_010,
        "nonce_store": store,
    }

    verify_request(**request)
    with pytest.raises(AuthenticationError) as raised:
        verify_request(**request)

    assert raised.value.code == "replayed_request"
