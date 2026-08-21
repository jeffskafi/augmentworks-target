from __future__ import annotations

from importlib.metadata import version

import pytest
from pydantic import ValidationError

from augmentworks_target import __version__
from augmentworks_target.models import (
    AssistantMessage,
    PrepareAttemptRequest,
    SendTurnResponse,
    ToolCallEvent,
)


def test_package_metadata_matches_runtime_version() -> None:
    assert version("augmentworks-target") == __version__


def test_wire_models_forbid_unknown_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        PrepareAttemptRequest(
            run_id="run_1",
            attempt_id="attempt_1",
            scenario_key="refund.approval",
            repetition_index=0,
            idempotency_key="prepare_1",
            unexpected=True,
        )

    with pytest.raises(ValidationError):
        PrepareAttemptRequest(
            run_id="run_1",
            attempt_id="attempt_1",
            scenario_key="refund.approval",
            repetition_index="0",
            idempotency_key="prepare_1",
        )


def test_event_union_uses_the_type_discriminator() -> None:
    response = SendTurnResponse.model_validate(
        {
            "turn_id": "turn_1",
            "message": {"role": "assistant", "content": "Refunded."},
            "events": [
                {
                    "type": "tool_call",
                    "event_id": "event_1",
                    "tool_name": "issue_refund",
                    "call_id": "call_1",
                    "arguments": {"order_id": "order_synthetic_001"},
                }
            ],
        }
    )

    assert isinstance(response.events[0], ToolCallEvent)
    assert response.message == AssistantMessage(content="Refunded.")


def test_hidden_reasoning_is_not_a_wire_field() -> None:
    with pytest.raises(ValidationError):
        SendTurnResponse.model_validate(
            {
                "turn_id": "turn_1",
                "message": {"role": "assistant", "content": "Final answer."},
                "chain_of_thought": "private reasoning",
            }
        )


def test_mutable_defaults_are_isolated() -> None:
    first = PrepareAttemptRequest(
        run_id="run_1",
        attempt_id="attempt_1",
        scenario_key="refund.approval",
        repetition_index=0,
        idempotency_key="prepare_1",
    )
    second = PrepareAttemptRequest(
        run_id="run_1",
        attempt_id="attempt_2",
        scenario_key="refund.approval",
        repetition_index=1,
        idempotency_key="prepare_2",
    )

    first.fixture["order_id"] = "order_synthetic_001"
    assert second.fixture == {}
