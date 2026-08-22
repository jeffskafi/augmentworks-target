"""Synthetic refund target used by the SDK quickstart and conformance CLI.

This intentionally uses an in-memory deterministic agent. Replace ``send``
with the call into your own application while preserving the target boundary.
Never load production customer data into this example.
"""

from __future__ import annotations

import copy
import os
from typing import Any

from fastapi import FastAPI

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
    TargetCapabilities,
    ToolCallEvent,
    ToolResultEvent,
)
from augmentworks_target.target import Target

AttemptState = dict[str, Any]
attempts: dict[str, AttemptState] = {}


def prepare(request: PrepareAttemptRequest) -> PrepareAttemptResponse:
    """Create isolated, synthetic state for one evaluation attempt."""

    attempts[request.attempt_id] = copy.deepcopy(dict(request.fixture))
    return PrepareAttemptResponse(attempt_id=request.attempt_id)


def send(attempt_id: str, request: SendTurnRequest) -> SendTurnResponse:
    """Run one turn and expose only final output plus observable tool events."""

    state = attempts[attempt_id]
    order = state["order"]
    policy = state["policy"]
    content = request.message.content.lower()

    if "refund" not in content:
        return SendTurnResponse(
            turn_id=request.turn_id,
            message=AssistantMessage(content="I can help with a synthetic refund request."),
        )

    order_id = str(order["id"])
    amount = float(order["amount"])
    call_id = f"{request.turn_id}:refund"
    call = ToolCallEvent(
        event_id=f"{call_id}:call",
        sequence=0,
        tool_name="issue_refund",
        call_id=call_id,
        arguments={"order_id": order_id, "amount": amount},
    )

    allowed = bool(order["refundable"]) and amount <= float(policy["maximum_refund"])
    if allowed:
        order["status"] = "refunded"
        order["refunded_amount"] = amount
        result = ToolResultEvent(
            event_id=f"{call_id}:result",
            sequence=1,
            tool_name="issue_refund",
            call_id=call_id,
            output={"order_id": order_id, "status": "refunded"},
            success=True,
        )
        answer = f"The synthetic order {order_id} was refunded."
    else:
        result = ToolResultEvent(
            event_id=f"{call_id}:result",
            sequence=1,
            tool_name="issue_refund",
            call_id=call_id,
            output={"order_id": order_id, "status": "denied"},
            success=False,
        )
        answer = f"The synthetic order {order_id} is not eligible for a refund."

    return SendTurnResponse(
        turn_id=request.turn_id,
        message=AssistantMessage(content=answer, finish_reason="stop"),
        events=[call, result],
        finished=True,
    )


def observe(attempt_id: str, request: ObserveAttemptRequest) -> ObserveAttemptResponse:
    """Return allow-listed state, never arbitrary application internals."""

    state = attempts[attempt_id]
    allowed_probes = {
        "order.status": state["order"]["status"],
        "order.refunded_amount": state["order"]["refunded_amount"],
        "order.refundable": state["order"]["refundable"],
    }
    observations = [
        Observation(key=key, value=allowed_probes[key], source="refund_fixture")
        for key in request.probe_keys
        if key in allowed_probes
    ]
    return ObserveAttemptResponse(
        request_id=request.request_id,
        observations=observations,
    )


def cleanup(attempt_id: str) -> None:
    """Delete all state associated with an attempt; safe to repeat."""

    attempts.pop(attempt_id, None)


secret = os.environ.get("AUGMENTWORKS_TARGET_SECRET")
if not secret:
    raise RuntimeError("Set AUGMENTWORKS_TARGET_SECRET before starting the synthetic target.")

key_id = os.environ.get("AUGMENTWORKS_TARGET_KEY_ID", "local-development")
target = Target(
    "synthetic-refund-agent",
    "0.1.0",
    prepare,
    send,
    observe=observe,
    cleanup=cleanup,
    capabilities=TargetCapabilities(observation=True, tool_events=True, cleanup=True),
)

app = FastAPI(title="Synthetic Refund Agent Target")
app.include_router(create_router(target, secrets={key_id: secret}))
