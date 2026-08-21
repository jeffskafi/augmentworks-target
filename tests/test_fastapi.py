from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from augmentworks_target.fastapi import create_router
from augmentworks_target.models import (
    AssistantMessage,
    Observation,
    ObserveAttemptRequest,
    ObserveAttemptResponse,
    PrepareAttemptRequest,
    PrepareAttemptResponse,
    SendTurnRequest,
    SendTurnResponse,
    ToolCallEvent,
    ToolResultEvent,
)
from augmentworks_target.security import sign_request
from augmentworks_target.target import Target, TargetRequestError

KEY_ID = "integration-key"
SECRET = "integration-secret-that-is-only-for-tests"


def build_app() -> tuple[FastAPI, dict[str, dict[str, Any]]]:
    attempts: dict[str, dict[str, Any]] = {}

    def prepare(request: PrepareAttemptRequest) -> PrepareAttemptResponse:
        attempts[request.attempt_id] = dict(request.fixture)
        return PrepareAttemptResponse(attempt_id=request.attempt_id)

    def send(attempt_id: str, request: SendTurnRequest) -> SendTurnResponse:
        if attempt_id not in attempts:
            raise TargetRequestError("attempt_not_found", "Attempt not found", status_code=404)
        attempts[attempt_id]["status"] = "refunded"
        return SendTurnResponse(
            turn_id=request.turn_id,
            message=AssistantMessage(content="Refunded."),
            events=[
                ToolCallEvent(
                    event_id="event_call",
                    tool_name="issue_refund",
                    call_id="call_1",
                    arguments={"order_id": "order_synthetic_001"},
                ),
                ToolResultEvent(
                    event_id="event_result",
                    tool_name="issue_refund",
                    call_id="call_1",
                    output={"status": "refunded"},
                ),
            ],
        )

    def observe(attempt_id: str, request: ObserveAttemptRequest) -> ObserveAttemptResponse:
        if attempt_id not in attempts:
            raise TargetRequestError("attempt_not_found", "Attempt not found", status_code=404)
        return ObserveAttemptResponse(
            request_id=request.request_id,
            observations=[Observation(key="order.status", value=attempts[attempt_id]["status"])],
        )

    def cleanup(attempt_id: str) -> None:
        attempts.pop(attempt_id, None)

    target = Target(
        "integration-target",
        "1.0",
        prepare,
        send,
        observe=observe,
        cleanup=cleanup,
    )
    app = FastAPI()
    app.include_router(create_router(target, secrets={KEY_ID: SECRET}))
    return app, attempts


def signed_request(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
):
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = sign_request(
        secret=SECRET,
        key_id=KEY_ID,
        method=method,
        path_with_query=path,
        body=body,
    )
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)


def prepare_payload() -> dict[str, Any]:
    return {
        "protocol_version": "aw-target/0.1",
        "run_id": "run_1",
        "attempt_id": "attempt_1",
        "scenario_key": "refund.approval",
        "repetition_index": 0,
        "idempotency_key": "prepare_1",
        "mode": "conformance",
        "fixture": {"status": "paid"},
        "metadata": {},
    }


def test_discovery_is_public_but_mutations_require_authentication() -> None:
    app, _ = build_app()

    with TestClient(app) as client:
        discovery = client.get("/.well-known/augmentworks-target.json")
        unauthenticated = client.post("/v1/attempts", json=prepare_payload())

    assert discovery.status_code == 200
    assert discovery.json()["protocol_version"] == "aw-target/0.1"
    assert discovery.json()["authentication"]["scheme"] == "hmac-sha256"
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "missing_auth_header"


def test_signed_prepare_send_observe_cleanup_lifecycle() -> None:
    app, attempts = build_app()

    with TestClient(app) as client:
        prepared = signed_request(client, "POST", "/v1/attempts", prepare_payload())
        turn = signed_request(
            client,
            "POST",
            "/v1/attempts/attempt_1/turns",
            {
                "protocol_version": "aw-target/0.1",
                "turn_id": "turn_1",
                "idempotency_key": "turn_key_1",
                "message": {"role": "user", "content": "Refund it."},
                "metadata": {},
            },
        )
        observed = signed_request(
            client,
            "POST",
            "/v1/attempts/attempt_1/observe",
            {
                "protocol_version": "aw-target/0.1",
                "request_id": "observation_1",
                "probe_keys": ["order.status"],
                "metadata": {},
            },
        )
        cleaned = signed_request(client, "DELETE", "/v1/attempts/attempt_1", None)

    assert prepared.status_code == 201
    assert prepared.json()["status"] == "ready"
    assert [event["type"] for event in turn.json()["events"]] == [
        "tool_call",
        "tool_result",
    ]
    assert observed.json()["observations"] == [
        {
            "key": "order.status",
            "value": "refunded",
            "source": "target",
            "authoritative": True,
        }
    ]
    assert cleaned.status_code == 204
    assert attempts == {}


def test_signature_binds_raw_body() -> None:
    app, attempts = build_app()
    signed_body = json.dumps(prepare_payload(), separators=(",", ":")).encode()
    headers = sign_request(
        secret=SECRET,
        key_id=KEY_ID,
        method="POST",
        path_with_query="/v1/attempts",
        body=signed_body,
    )

    tampered = prepare_payload()
    tampered["attempt_id"] = "tampered_attempt"
    tampered_body = json.dumps(tampered, separators=(",", ":")).encode()
    with TestClient(app) as client:
        response = client.post(
            "/v1/attempts",
            content=tampered_body,
            headers={**headers, "Content-Type": "application/json"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert attempts == {}


def test_nonce_replay_is_rejected() -> None:
    app, _ = build_app()
    body = json.dumps(prepare_payload(), separators=(",", ":")).encode()
    headers = sign_request(
        secret=SECRET,
        key_id=KEY_ID,
        method="POST",
        path_with_query="/v1/attempts",
        body=body,
    )
    headers["Content-Type"] = "application/json"

    with TestClient(app) as client:
        first = client.post("/v1/attempts", content=body, headers=headers)
        replay = client.post("/v1/attempts", content=body, headers=headers)

    assert first.status_code == 201
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "replayed_request"
