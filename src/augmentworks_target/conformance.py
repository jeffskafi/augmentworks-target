"""Protocol conformance checks for an AugmentWorks target endpoint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from .models import PROTOCOL_VERSION
from .security import sign_request

DISCOVERY_PATH = "/.well-known/augmentworks-target.json"
ATTEMPTS_PATH = "/v1/attempts"


@dataclass(frozen=True, slots=True)
class ConformanceCheck:
    """The result of one independently useful conformance assertion."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(slots=True)
class ConformanceReport:
    """A serializable collection of target conformance checks."""

    base_url: str
    checks: list[ConformanceCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(ConformanceCheck(name, passed, detail))

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
        }


async def verify_target(
    base_url: str,
    *,
    key_id: str | None = None,
    secret: str | bytes | None = None,
    fixture: Mapping[str, Any] | None = None,
    message: str = "Please refund order_synthetic_001.",
    probe_keys: list[str] | None = None,
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> ConformanceReport:
    """Verify discovery and, when a fixture is supplied, the full lifecycle.

    Supplying ``fixture`` opts into a stateful check that prepares an attempt,
    sends one user turn, observes state, and cleans up. Cleanup is attempted in
    a ``finally`` block even when an earlier lifecycle operation fails.
    """

    normalized_url = base_url.rstrip("/")
    report = ConformanceReport(base_url=normalized_url)
    owns_client = client is None
    http = client or httpx.AsyncClient(base_url=normalized_url, timeout=timeout_seconds)

    try:
        discovery_ok = await _check_discovery(http, report)
        if not discovery_ok or fixture is None:
            return report

        if not key_id or secret is None:
            report.add(
                "lifecycle.credentials",
                False,
                "A key ID and secret are required when a fixture is supplied.",
            )
            return report

        await _check_lifecycle(
            http,
            report,
            key_id=key_id,
            secret=secret,
            fixture=fixture,
            message=message,
            probe_keys=probe_keys or ["order.status", "order.refunded_amount"],
        )
        return report
    finally:
        if owns_client:
            await http.aclose()


async def _check_discovery(client: httpx.AsyncClient, report: ConformanceReport) -> bool:
    try:
        response = await client.get(DISCOVERY_PATH)
    except httpx.HTTPError as exc:
        report.add("discovery.http", False, f"Request failed: {exc.__class__.__name__}")
        return False

    if response.status_code != 200:
        report.add(
            "discovery.http",
            False,
            f"Expected HTTP 200, received HTTP {response.status_code}.",
        )
        return False

    report.add("discovery.http", True, "Discovery endpoint returned HTTP 200.")
    payload = _json_object(response)
    if payload is None:
        report.add("discovery.document", False, "Response was not a JSON object.")
        return False

    version = payload.get("protocol_version")
    if version != PROTOCOL_VERSION:
        report.add(
            "discovery.protocol_version",
            False,
            f"Expected {PROTOCOL_VERSION!r}, received {version!r}.",
        )
        return False

    report.add(
        "discovery.protocol_version",
        True,
        f"Target advertises {PROTOCOL_VERSION}.",
    )
    return True


async def _check_lifecycle(
    client: httpx.AsyncClient,
    report: ConformanceReport,
    *,
    key_id: str,
    secret: str | bytes,
    fixture: Mapping[str, Any],
    message: str,
    probe_keys: list[str],
) -> None:
    attempt_id = f"attempt_{uuid4().hex}"
    run_id = f"run_{uuid4().hex}"
    prepared = False

    try:
        prepare_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "scenario_key": "augmentworks.conformance.synthetic",
            "repetition_index": 0,
            "idempotency_key": f"{attempt_id}:prepare",
            "mode": "conformance",
            "fixture": dict(fixture),
            "metadata": {"source": "augmentworks-target-conformance"},
        }
        prepare = await _signed_json_request(
            client,
            "POST",
            ATTEMPTS_PATH,
            prepare_payload,
            key_id=key_id,
            secret=secret,
        )
        prepared = _check_response(
            report,
            "lifecycle.prepare",
            prepare,
            expected_status=201,
            expected_fields={"attempt_id": attempt_id, "status": "ready"},
        )
        if not prepared:
            return

        turn_id = f"turn_{uuid4().hex}"
        turn_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "turn_id": turn_id,
            "idempotency_key": f"{attempt_id}:{turn_id}",
            "message": {"role": "user", "content": message},
            "metadata": {},
        }
        turn = await _signed_json_request(
            client,
            "POST",
            f"{ATTEMPTS_PATH}/{attempt_id}/turns",
            turn_payload,
            key_id=key_id,
            secret=secret,
        )
        if not _check_response(
            report,
            "lifecycle.send",
            turn,
            expected_status=200,
            expected_fields={"turn_id": turn_id},
        ):
            return

        request_id = f"observation_{uuid4().hex}"
        observe_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "probe_keys": probe_keys,
            "metadata": {},
        }
        observe = await _signed_json_request(
            client,
            "POST",
            f"{ATTEMPTS_PATH}/{attempt_id}/observe",
            observe_payload,
            key_id=key_id,
            secret=secret,
        )
        _check_response(
            report,
            "lifecycle.observe",
            observe,
            expected_status=200,
            expected_fields={"request_id": request_id},
        )
    except httpx.HTTPError as exc:
        report.add(
            "lifecycle.transport",
            False,
            f"Request failed: {exc.__class__.__name__}",
        )
    finally:
        # A target can commit state before its response reaches the caller, so
        # cleanup is useful even after an apparently failed prepare request.
        try:
            cleanup = await _signed_json_request(
                client,
                "DELETE",
                f"{ATTEMPTS_PATH}/{attempt_id}",
                None,
                key_id=key_id,
                secret=secret,
            )
            detail = "Prepared attempt was removed." if prepared else "Cleanup was safe."
            report.add(
                "lifecycle.cleanup",
                cleanup.status_code == 204,
                detail
                if cleanup.status_code == 204
                else f"Expected HTTP 204, received HTTP {cleanup.status_code}.",
            )
        except httpx.HTTPError as exc:
            report.add(
                "lifecycle.cleanup",
                False,
                f"Cleanup request failed: {exc.__class__.__name__}",
            )


async def _signed_json_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    *,
    key_id: str,
    secret: str | bytes,
) -> httpx.Response:
    body = (
        b""
        if payload is None
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    )
    headers = sign_request(
        secret=secret,
        key_id=key_id,
        method=method,
        path_with_query=path,
        body=body,
    )
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return await client.request(method, path, content=body, headers=headers)


def _check_response(
    report: ConformanceReport,
    name: str,
    response: httpx.Response,
    *,
    expected_status: int,
    expected_fields: Mapping[str, Any],
) -> bool:
    if response.status_code != expected_status:
        report.add(
            name,
            False,
            f"Expected HTTP {expected_status}, received HTTP {response.status_code}.",
        )
        return False

    payload = _json_object(response)
    if payload is None:
        report.add(name, False, "Response was not a JSON object.")
        return False

    mismatches = {
        key: (value, payload.get(key))
        for key, value in expected_fields.items()
        if payload.get(key) != value
    }
    if mismatches:
        fields = ", ".join(sorted(mismatches))
        report.add(name, False, f"Response contained unexpected fields: {fields}.")
        return False

    report.add(name, True, f"Endpoint returned HTTP {expected_status} and valid IDs.")
    return True


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
