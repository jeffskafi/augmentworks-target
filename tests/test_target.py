from __future__ import annotations

import asyncio

import pytest

from augmentworks_target.models import (
    AssistantMessage,
    ObserveAttemptRequest,
    PrepareAttemptRequest,
    SendTurnRequest,
    SendTurnResponse,
    TargetCapabilities,
    ToolCallEvent,
    UserMessage,
)
from augmentworks_target.target import Target, TargetRequestError


def prepare_request() -> PrepareAttemptRequest:
    return PrepareAttemptRequest(
        run_id="run_1",
        attempt_id="attempt_1",
        scenario_key="refund.approval",
        repetition_index=0,
        idempotency_key="prepare_1",
    )


def turn_request() -> SendTurnRequest:
    return SendTurnRequest(
        turn_id="turn_1",
        idempotency_key="turn_key_1",
        message=UserMessage(content="Please refund the order."),
    )


def test_target_accepts_sync_and_async_callbacks_returning_mappings() -> None:
    cleaned: list[str] = []

    def prepare(request: PrepareAttemptRequest) -> dict[str, object]:
        return {"attempt_id": request.attempt_id}

    async def send(attempt_id: str, request: SendTurnRequest) -> dict[str, object]:
        assert attempt_id == "attempt_1"
        return {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": "Done."},
        }

    async def cleanup(attempt_id: str) -> None:
        cleaned.append(attempt_id)

    target = Target("test-target", "1", prepare, send, cleanup=cleanup)

    prepared = asyncio.run(target.prepare(prepare_request()))
    sent = asyncio.run(target.send("attempt_1", turn_request()))
    asyncio.run(target.cleanup("attempt_1"))

    assert prepared.attempt_id == "attempt_1"
    assert sent.message.content == "Done."
    assert cleaned == ["attempt_1"]
    assert target.discovery.capabilities.cleanup is True
    assert target.discovery.capabilities.tool_events is False


def test_target_infers_observation_capability() -> None:
    def prepare(request: PrepareAttemptRequest) -> dict[str, object]:
        return {"attempt_id": request.attempt_id}

    def send(attempt_id: str, request: SendTurnRequest) -> dict[str, object]:
        return {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": attempt_id},
        }

    def observe(attempt_id: str, request: ObserveAttemptRequest) -> dict[str, object]:
        return {"request_id": request.request_id, "observations": []}

    target = Target("test-target", "1", prepare, send, observe=observe)
    request = ObserveAttemptRequest(request_id="observation_1")

    response = asyncio.run(target.observe("attempt_1", request))

    assert response.request_id == "observation_1"
    assert target.discovery.capabilities.observation is True


def test_missing_observe_callback_returns_public_error() -> None:
    target = Target(
        "test-target",
        "1",
        lambda request: {"attempt_id": request.attempt_id},
        lambda attempt_id, request: {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": attempt_id},
        },
    )

    with pytest.raises(TargetRequestError) as raised:
        asyncio.run(target.observe("attempt_1", ObserveAttemptRequest(request_id="observation_1")))

    assert raised.value.code == "observation_not_supported"


def test_missing_cleanup_callback_is_not_advertised_or_silently_accepted() -> None:
    target = Target(
        "test-target",
        "1",
        lambda request: {"attempt_id": request.attempt_id},
        lambda attempt_id, request: {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": attempt_id},
        },
    )

    assert target.discovery.capabilities.cleanup is False
    with pytest.raises(TargetRequestError) as raised:
        asyncio.run(target.cleanup("attempt_1"))
    assert raised.value.code == "cleanup_not_supported"


@pytest.mark.parametrize(
    ("capabilities", "with_observe", "with_cleanup", "field"),
    [
        (TargetCapabilities(observation=True), False, False, "observation"),
        (TargetCapabilities(observation=False), True, False, "observation"),
        (TargetCapabilities(cleanup=True), False, False, "cleanup"),
        (TargetCapabilities(cleanup=False), False, True, "cleanup"),
    ],
)
def test_explicit_callback_capability_contradictions_fail_at_startup(
    capabilities: TargetCapabilities,
    with_observe: bool,
    with_cleanup: bool,
    field: str,
) -> None:
    def prepare(request: PrepareAttemptRequest) -> dict[str, object]:
        return {"attempt_id": request.attempt_id}

    def send(attempt_id: str, request: SendTurnRequest) -> dict[str, object]:
        return {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": attempt_id},
        }

    def observe(attempt_id: str, request: ObserveAttemptRequest) -> dict[str, object]:
        return {"request_id": request.request_id, "observations": []}

    def cleanup(attempt_id: str) -> None:
        return None

    with pytest.raises(ValueError, match=field):
        Target(
            "test-target",
            "1",
            prepare,
            send,
            observe=observe if with_observe else None,
            cleanup=cleanup if with_cleanup else None,
            capabilities=capabilities,
        )


def test_tool_events_are_opt_in_and_undeclared_events_fail_closed() -> None:
    def prepare(request: PrepareAttemptRequest) -> dict[str, object]:
        return {"attempt_id": request.attempt_id}

    def send(attempt_id: str, request: SendTurnRequest) -> SendTurnResponse:
        return SendTurnResponse(
            turn_id=request.turn_id,
            message=AssistantMessage(content="Done."),
            events=[
                ToolCallEvent(
                    event_id="event_1",
                    tool_name="issue_refund",
                    call_id="call_1",
                )
            ],
        )

    target = Target("test-target", "1", prepare, send)

    assert target.discovery.capabilities.tool_events is False
    with pytest.raises(RuntimeError, match="does not declare tool_events"):
        asyncio.run(target.send("attempt_1", turn_request()))


def test_explicit_tool_event_capability_allows_structured_events() -> None:
    def prepare(request: PrepareAttemptRequest) -> dict[str, object]:
        return {"attempt_id": request.attempt_id}

    def send(attempt_id: str, request: SendTurnRequest) -> SendTurnResponse:
        return SendTurnResponse(
            turn_id=request.turn_id,
            message=AssistantMessage(content="Done."),
            events=[
                ToolCallEvent(
                    event_id="event_1",
                    tool_name="issue_refund",
                    call_id="call_1",
                )
            ],
        )

    target = Target(
        "test-target",
        "1",
        prepare,
        send,
        capabilities=TargetCapabilities(tool_events=True),
    )

    response = asyncio.run(target.send("attempt_1", turn_request()))

    assert target.discovery.capabilities.tool_events is True
    assert isinstance(response.events[0], ToolCallEvent)


def test_callback_cannot_change_protocol_identifier() -> None:
    target = Target(
        "test-target",
        "1",
        lambda request: {"attempt_id": "different_attempt"},
        lambda attempt_id, request: {
            "turn_id": request.turn_id,
            "message": {"role": "assistant", "content": attempt_id},
        },
    )

    with pytest.raises(RuntimeError, match="different attempt_id"):
        asyncio.run(target.prepare(prepare_request()))
