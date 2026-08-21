"""Framework-neutral callback wrapper for a customer AI application."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from .models import (
    ObserveAttemptRequest,
    ObserveAttemptResponse,
    PrepareAttemptRequest,
    PrepareAttemptResponse,
    SendTurnRequest,
    SendTurnResponse,
    TargetCapabilities,
    TargetDiscovery,
    TargetLimits,
)
from .security import NonceStore, SecretResolver

if TYPE_CHECKING:
    from fastapi import APIRouter


ModelT = TypeVar("ModelT", bound=BaseModel)
PrepareCallback = Callable[
    [PrepareAttemptRequest],
    PrepareAttemptResponse
    | Mapping[str, object]
    | Awaitable[PrepareAttemptResponse | Mapping[str, object]],
]
SendCallback = Callable[
    [str, SendTurnRequest],
    SendTurnResponse | Mapping[str, object] | Awaitable[SendTurnResponse | Mapping[str, object]],
]
ObserveCallback = Callable[
    [str, ObserveAttemptRequest],
    ObserveAttemptResponse
    | Mapping[str, object]
    | Awaitable[ObserveAttemptResponse | Mapping[str, object]],
]
CleanupCallback = Callable[[str], None | Awaitable[None]]


class TargetRequestError(Exception):
    """An intentionally public, sanitized error raised by target callbacks.

    Use this only for expected client-facing failures such as an unknown
    attempt.  Unexpected exceptions are logged by the host application and
    converted to a generic response by the HTTP adapter.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        if status_code < 400 or status_code > 499:
            raise ValueError("TargetRequestError status_code must be a 4xx status")
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class Target:
    """Connect application callbacks to the AugmentWorks target protocol.

    Callbacks may be ordinary functions or ``async`` functions.  They may
    return the corresponding Pydantic response model or a mapping that validates
    as that model.  The SDK deliberately does not require an LLM provider,
    agent framework, or model API key.
    """

    def __init__(
        self,
        name: str,
        version: str,
        prepare: PrepareCallback,
        send: SendCallback,
        observe: ObserveCallback | None = None,
        cleanup: CleanupCallback | None = None,
        *,
        capabilities: TargetCapabilities | None = None,
        limits: TargetLimits | None = None,
    ) -> None:
        inferred_capabilities = TargetCapabilities(
            multi_turn=True,
            observation=observe is not None,
            tool_events=True,
            cleanup=True,
        )
        self._discovery = TargetDiscovery(
            target_name=name,
            target_version=version,
            capabilities=capabilities or inferred_capabilities,
            limits=limits or TargetLimits(),
        )
        self._prepare_callback = prepare
        self._send_callback = send
        self._observe_callback = observe
        self._cleanup_callback = cleanup

    @property
    def discovery(self) -> TargetDiscovery:
        """Return an immutable-by-convention discovery snapshot."""

        return self._discovery.model_copy(deep=True)

    async def prepare(self, request: PrepareAttemptRequest) -> PrepareAttemptResponse:
        result = await _call(self._prepare_callback, request)
        response = _validate_result(PrepareAttemptResponse, result)
        if response.attempt_id != request.attempt_id:
            raise RuntimeError("prepare callback returned a different attempt_id")
        return response

    async def send(self, attempt_id: str, request: SendTurnRequest) -> SendTurnResponse:
        result = await _call(self._send_callback, attempt_id, request)
        response = _validate_result(SendTurnResponse, result)
        if response.turn_id != request.turn_id:
            raise RuntimeError("send callback returned a different turn_id")
        return response

    async def observe(
        self, attempt_id: str, request: ObserveAttemptRequest
    ) -> ObserveAttemptResponse:
        if self._observe_callback is None:
            raise TargetRequestError(
                "observation_not_supported",
                "This target does not support state observation",
                status_code=404,
            )
        result = await _call(self._observe_callback, attempt_id, request)
        response = _validate_result(ObserveAttemptResponse, result)
        if response.request_id != request.request_id:
            raise RuntimeError("observe callback returned a different request_id")
        return response

    async def cleanup(self, attempt_id: str) -> None:
        if self._cleanup_callback is not None:
            result = self._cleanup_callback(attempt_id)
            if inspect.isawaitable(result):
                await result

    def fastapi_router(
        self,
        *,
        secrets: SecretResolver,
        nonce_store: NonceStore | None = None,
        max_clock_skew_seconds: int = 300,
    ) -> APIRouter:
        """Create a FastAPI router without importing FastAPI at package import.

        Applications may instead import
        :func:`augmentworks_target.fastapi.create_router` directly.
        """

        from .fastapi import create_router

        return create_router(
            self,
            secrets=secrets,
            nonce_store=nonce_store,
            max_clock_skew_seconds=max_clock_skew_seconds,
        )


async def _call(callback: Callable[..., object], *args: object) -> object:
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _validate_result(model: type[ModelT], value: object) -> ModelT:
    if isinstance(value, model):
        return value
    return model.model_validate(value)
