"""Strict wire models for the AugmentWorks target protocol.

The protocol intentionally models only externally observable behavior.  It has
no field for hidden reasoning, scratchpads, or chain-of-thought.  Target
adapters should return final assistant messages, tool boundaries, and explicit
state observations only.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ProtocolVersion = Literal["aw-target/0.1"]
PROTOCOL_VERSION: Final[ProtocolVersion] = "aw-target/0.1"


class ProtocolModel(BaseModel):
    """Base class for protocol payloads.

    ``extra="forbid"`` is important at this boundary: misspelled fields and
    unsupported extensions fail closed instead of silently changing an eval.
    ``strict=True`` prevents coercions such as ``"1"`` into ``1``.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class TargetCapabilities(ProtocolModel):
    """Behavior supported by a target adapter."""

    multi_turn: bool = True
    observation: bool = False
    tool_events: bool = False
    cleanup: bool = False


class TargetLimits(ProtocolModel):
    """Limits an evaluator should enforce before sending work."""

    max_request_bytes: int = Field(default=1_048_576, ge=1)
    max_turns_per_attempt: int = Field(default=50, ge=1)


class AuthenticationDescriptor(ProtocolModel):
    """The authentication contract advertised during discovery."""

    scheme: Literal["hmac-sha256"] = "hmac-sha256"
    key_id_header: Literal["AW-Key-Id"] = "AW-Key-Id"
    timestamp_header: Literal["AW-Timestamp"] = "AW-Timestamp"
    nonce_header: Literal["AW-Nonce"] = "AW-Nonce"
    signature_header: Literal["AW-Signature"] = "AW-Signature"


class TargetDiscovery(ProtocolModel):
    """Response from ``/.well-known/augmentworks-target.json``."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    target_name: str = Field(min_length=1, max_length=200)
    target_version: str = Field(min_length=1, max_length=100)
    capabilities: TargetCapabilities = Field(default_factory=TargetCapabilities)
    limits: TargetLimits = Field(default_factory=TargetLimits)
    authentication: AuthenticationDescriptor = Field(default_factory=AuthenticationDescriptor)


class PrepareAttemptRequest(ProtocolModel):
    """Create isolated state for one scenario repetition."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    run_id: str = Field(min_length=1, max_length=200)
    attempt_id: str = Field(min_length=1, max_length=200)
    scenario_key: str = Field(min_length=1, max_length=300)
    repetition_index: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=300)
    mode: Literal["evaluation", "conformance"] = "evaluation"
    fixture: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class PrepareAttemptResponse(ProtocolModel):
    """Confirmation that an attempt is ready to receive turns."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    status: Literal["ready"] = "ready"
    attempt_id: str = Field(min_length=1, max_length=200)
    target_session_id: str | None = Field(default=None, min_length=1, max_length=300)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class UserMessage(ProtocolModel):
    """A user-visible input turn."""

    role: Literal["user"] = "user"
    content: str = Field(min_length=1)


class AssistantMessage(ProtocolModel):
    """The final, user-visible assistant output for a turn."""

    role: Literal["assistant"] = "assistant"
    content: str
    finish_reason: str | None = Field(default=None, max_length=100)


class ToolCallEvent(ProtocolModel):
    """An observable request by the application to invoke a tool."""

    type: Literal["tool_call"] = "tool_call"
    event_id: str = Field(min_length=1, max_length=300)
    sequence: int | None = Field(default=None, ge=0)
    tool_name: str = Field(min_length=1, max_length=300)
    call_id: str = Field(min_length=1, max_length=300)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResultEvent(ProtocolModel):
    """An observable result returned by a tool boundary."""

    type: Literal["tool_result"] = "tool_result"
    event_id: str = Field(min_length=1, max_length=300)
    sequence: int | None = Field(default=None, ge=0)
    tool_name: str = Field(min_length=1, max_length=300)
    call_id: str = Field(min_length=1, max_length=300)
    output: JsonValue = None
    success: bool = True


class HandoffEvent(ProtocolModel):
    """An explicit handoff or escalation visible to the application."""

    type: Literal["handoff"] = "handoff"
    event_id: str = Field(min_length=1, max_length=300)
    sequence: int | None = Field(default=None, ge=0)
    destination: str = Field(min_length=1, max_length=300)
    reason: str | None = None


class ErrorEvent(ProtocolModel):
    """A safe, externally observable application error.

    ``message`` must not contain stack traces, secrets, or hidden model
    reasoning.
    """

    type: Literal["error"] = "error"
    event_id: str = Field(min_length=1, max_length=300)
    sequence: int | None = Field(default=None, ge=0)
    code: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    retryable: bool = False


TargetEvent = Annotated[
    ToolCallEvent | ToolResultEvent | HandoffEvent | ErrorEvent,
    Field(discriminator="type"),
]


class SendTurnRequest(ProtocolModel):
    """Send one user turn to an existing attempt."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    turn_id: str = Field(min_length=1, max_length=300)
    idempotency_key: str = Field(min_length=1, max_length=300)
    message: UserMessage
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SendTurnResponse(ProtocolModel):
    """Final assistant output plus safe, structured behavioral events."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    turn_id: str = Field(min_length=1, max_length=300)
    message: AssistantMessage
    events: list[TargetEvent] = Field(default_factory=list)
    finished: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ObserveAttemptRequest(ProtocolModel):
    """Request allow-listed state probes after one or more turns."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=300)
    probe_keys: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Observation(ProtocolModel):
    """One externally observable state value."""

    key: str = Field(min_length=1, max_length=300)
    value: JsonValue = None
    source: str = Field(default="target", min_length=1, max_length=200)
    authoritative: bool = True


class ObserveAttemptResponse(ProtocolModel):
    """State observations for the requested attempt."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=300)
    observations: list[Observation] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorDetail(ProtocolModel):
    """Sanitized API error safe to return over the target boundary."""

    code: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    retryable: bool = False


class ErrorResponse(ProtocolModel):
    """Envelope used for all non-validation protocol errors."""

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    error: ErrorDetail
