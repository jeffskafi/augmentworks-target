"""FastAPI transport for :class:`augmentworks_target.target.Target`."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TypeVar

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from .models import (
    ErrorDetail,
    ErrorResponse,
    ObserveAttemptRequest,
    PrepareAttemptRequest,
    SendTurnRequest,
)
from .security import (
    AuthenticationError,
    MemoryNonceStore,
    NonceStore,
    SecretResolver,
    verify_request,
)
from .target import Target, TargetRequestError

logger = logging.getLogger(__name__)
RequestModel = TypeVar("RequestModel", bound=BaseModel)


def create_router(
    target: Target,
    *,
    secrets: SecretResolver,
    nonce_store: NonceStore | None = None,
    max_clock_skew_seconds: int = 300,
) -> APIRouter:
    """Expose a target through the standard AugmentWorks HTTP surface.

    ``secrets`` maps key IDs to shared HMAC secrets, or is a callable resolver.
    Pass a distributed, atomic ``nonce_store`` in multi-process production
    deployments.  The process-local default is intended for development only.
    """

    if not callable(secrets) and not isinstance(secrets, Mapping):
        raise TypeError("secrets must be a mapping or callable resolver")
    if max_clock_skew_seconds < 1:
        raise ValueError("max_clock_skew_seconds must be positive")

    replay_store = MemoryNonceStore() if nonce_store is None else nonce_store
    router = APIRouter()

    @router.get("/.well-known/augmentworks-target.json")
    async def discovery() -> JSONResponse:
        return _json_response(target.discovery)

    @router.post("/v1/attempts")
    async def prepare_attempt(request: Request) -> JSONResponse:
        try:
            body = await _authenticated_body(
                request,
                secrets=secrets,
                nonce_store=replay_store,
                max_clock_skew_seconds=max_clock_skew_seconds,
                max_request_bytes=target.discovery.limits.max_request_bytes,
            )
            payload = _parse_body(PrepareAttemptRequest, body)
            return _json_response(await target.prepare(payload), status_code=201)
        except AuthenticationError as exc:
            return _authentication_error(exc)
        except TargetRequestError as exc:
            return _target_error(exc)
        except ValidationError:
            return _error(
                422,
                "invalid_request",
                "Request body does not match the target protocol schema",
            )
        except Exception:
            logger.exception("Unexpected error while preparing target attempt")
            return _internal_error()

    @router.post("/v1/attempts/{attempt_id}/turns")
    async def send_turn(attempt_id: str, request: Request) -> JSONResponse:
        try:
            body = await _authenticated_body(
                request,
                secrets=secrets,
                nonce_store=replay_store,
                max_clock_skew_seconds=max_clock_skew_seconds,
                max_request_bytes=target.discovery.limits.max_request_bytes,
            )
            payload = _parse_body(SendTurnRequest, body)
            return _json_response(await target.send(attempt_id, payload))
        except AuthenticationError as exc:
            return _authentication_error(exc)
        except TargetRequestError as exc:
            return _target_error(exc)
        except ValidationError:
            return _error(
                422,
                "invalid_request",
                "Request body does not match the target protocol schema",
            )
        except Exception:
            logger.exception("Unexpected error while sending target turn")
            return _internal_error()

    @router.post("/v1/attempts/{attempt_id}/observe")
    async def observe_attempt(attempt_id: str, request: Request) -> JSONResponse:
        try:
            body = await _authenticated_body(
                request,
                secrets=secrets,
                nonce_store=replay_store,
                max_clock_skew_seconds=max_clock_skew_seconds,
                max_request_bytes=target.discovery.limits.max_request_bytes,
            )
            payload = _parse_body(ObserveAttemptRequest, body)
            return _json_response(await target.observe(attempt_id, payload))
        except AuthenticationError as exc:
            return _authentication_error(exc)
        except TargetRequestError as exc:
            return _target_error(exc)
        except ValidationError:
            return _error(
                422,
                "invalid_request",
                "Request body does not match the target protocol schema",
            )
        except Exception:
            logger.exception("Unexpected error while observing target attempt")
            return _internal_error()

    @router.delete("/v1/attempts/{attempt_id}")
    async def cleanup_attempt(attempt_id: str, request: Request) -> Response:
        try:
            await _authenticated_body(
                request,
                secrets=secrets,
                nonce_store=replay_store,
                max_clock_skew_seconds=max_clock_skew_seconds,
                max_request_bytes=target.discovery.limits.max_request_bytes,
            )
            await target.cleanup(attempt_id)
            return Response(status_code=204)
        except AuthenticationError as exc:
            return _authentication_error(exc)
        except TargetRequestError as exc:
            return _target_error(exc)
        except Exception:
            logger.exception("Unexpected error while cleaning up target attempt")
            return _internal_error()

    return router


async def _authenticated_body(
    request: Request,
    *,
    secrets: SecretResolver,
    nonce_store: NonceStore,
    max_clock_skew_seconds: int,
    max_request_bytes: int,
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise ValueError
            if declared_length > max_request_bytes:
                raise TargetRequestError(
                    "request_too_large",
                    "Request exceeds the target's advertised size limit",
                    status_code=413,
                )
        except ValueError as exc:
            raise TargetRequestError(
                "invalid_content_length",
                "Content-Length must be an integer",
                status_code=400,
            ) from exc

    body_buffer = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(chunk) > max_request_bytes - len(body_buffer):
            raise TargetRequestError(
                "request_too_large",
                "Request exceeds the target's advertised size limit",
                status_code=413,
            )
        body_buffer.extend(chunk)
    body = bytes(body_buffer)

    verify_request(
        headers=request.headers,
        secrets=secrets,
        method=request.method,
        path_with_query=_path_with_query(request),
        body=body,
        nonce_store=nonce_store,
        max_clock_skew_seconds=max_clock_skew_seconds,
    )
    return body


def _path_with_query(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes):
        path = raw_path.decode("latin-1")
    else:
        path = request.url.path

    query = request.scope.get("query_string", b"")
    if isinstance(query, bytes) and query:
        return f"{path}?{query.decode('latin-1')}"
    return path


def _parse_body(model: type[RequestModel], body: bytes) -> RequestModel:
    # ``model_validate_json`` preserves strict JSON validation and avoids
    # placing rejected request values into framework-generated error details.
    return model.model_validate_json(body)


def _json_response(model: BaseModel, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=model.model_dump(mode="json"))


def _authentication_error(exc: AuthenticationError) -> JSONResponse:
    response = _error(401, exc.code, exc.message)
    response.headers["WWW-Authenticate"] = "HMAC"
    return response


def _target_error(exc: TargetRequestError) -> JSONResponse:
    return _error(exc.status_code, exc.code, exc.message, retryable=exc.retryable)


def _internal_error() -> JSONResponse:
    return _error(
        500,
        "internal_error",
        "The target could not complete the request",
        retryable=True,
    )


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message, retryable=retryable))
    return _json_response(payload, status_code=status_code)
