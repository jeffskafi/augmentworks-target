from __future__ import annotations

import asyncio
import json

import httpx

from augmentworks_target.conformance import verify_target

SECRET = "conformance-secret-only-for-tests"


def discovery_response() -> dict[str, object]:
    return {
        "protocol_version": "aw-target/0.1",
        "target_name": "mock-target",
        "target_version": "1",
    }


def test_verify_target_runs_complete_signed_lifecycle() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=discovery_response())

        assert request.headers["AW-Key-Id"] == "conformance-key"
        assert request.headers["AW-Signature"].startswith("v1=")
        if request.url.path == "/v1/attempts":
            payload = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "protocol_version": "aw-target/0.1",
                    "status": "ready",
                    "attempt_id": payload["attempt_id"],
                },
            )
        if request.url.path.endswith("/turns"):
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "protocol_version": "aw-target/0.1",
                    "turn_id": payload["turn_id"],
                    "message": {"role": "assistant", "content": "Done."},
                },
            )
        if request.url.path.endswith("/observe"):
            payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "protocol_version": "aw-target/0.1",
                    "request_id": payload["request_id"],
                    "observations": [],
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://target.test"
        ) as client:
            report = await verify_target(
                "https://target.test",
                key_id="conformance-key",
                secret=SECRET,
                fixture={"order": {"status": "paid"}},
                client=client,
            )

        assert report.passed is True
        assert all(check.passed for check in report.checks)

    asyncio.run(run())
    assert requests == [
        ("GET", "/.well-known/augmentworks-target.json"),
        ("POST", "/v1/attempts"),
        ("POST", requests[2][1]),
        ("POST", requests[3][1]),
        ("DELETE", requests[4][1]),
    ]
    assert requests[2][1].endswith("/turns")
    assert requests[3][1].endswith("/observe")


def test_verify_target_attempts_cleanup_after_send_failure() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json=discovery_response())
        if request.url.path == "/v1/attempts":
            payload = json.loads(request.content)
            return httpx.Response(
                201, json={"status": "ready", "attempt_id": payload["attempt_id"]}
            )
        if request.url.path.endswith("/turns"):
            return httpx.Response(500, json={"error": {"code": "synthetic_failure"}})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://target.test"
        ) as client:
            report = await verify_target(
                "https://target.test",
                key_id="conformance-key",
                secret=SECRET,
                fixture={},
                client=client,
            )

        assert report.passed is False
        cleanup = next(check for check in report.checks if check.name == "lifecycle.cleanup")
        assert cleanup.passed is True

    asyncio.run(run())
    assert requested_paths[-1].startswith("/v1/attempts/")
    assert not requested_paths[-1].endswith(("/turns", "/observe"))


def test_discovery_only_check_does_not_require_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=discovery_response())

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://target.test"
        ) as client:
            report = await verify_target("https://target.test", client=client)
        assert report.passed is True

    asyncio.run(run())
