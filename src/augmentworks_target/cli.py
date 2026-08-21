"""Command-line entry point for target conformance checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .conformance import ConformanceReport, verify_target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="augmentworks-target",
        description="Validate an AugmentWorks target adapter.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser(
        "verify", help="Check discovery or a complete synthetic lifecycle."
    )
    verify.add_argument("base_url", help="Base URL of the target adapter.")
    verify.add_argument(
        "--fixture",
        type=Path,
        help="JSON fixture. Supplying it enables the full lifecycle check.",
    )
    verify.add_argument("--key-id", help="HMAC key ID configured by the target.")
    verify.add_argument(
        "--secret-env",
        default="AUGMENTWORKS_TARGET_SECRET",
        metavar="NAME",
        help=(
            "Environment variable containing the HMAC secret (default: AUGMENTWORKS_TARGET_SECRET)."
        ),
    )
    verify.add_argument(
        "--message",
        default="Please refund order_synthetic_001.",
        help="Synthetic user message for the lifecycle check.",
    )
    verify.add_argument(
        "--probe-key",
        action="append",
        dest="probe_keys",
        help="State key to observe; may be repeated.",
    )
    verify.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="Per-request timeout (default: 10).",
    )
    verify.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "verify":  # pragma: no cover - argparse enforces this
        return 2

    fixture = None
    secret = None
    if args.fixture is not None:
        if not args.key_id:
            return _configuration_error("--key-id is required with --fixture.")

        secret = os.environ.get(args.secret_env)
        if secret is None:
            return _configuration_error(f"Environment variable {args.secret_env!r} is not set.")

        try:
            fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        except OSError as exc:
            return _configuration_error(f"Could not read fixture: {exc}.")
        except json.JSONDecodeError as exc:
            return _configuration_error(
                f"Fixture is not valid JSON (line {exc.lineno}, column {exc.colno})."
            )
        if not isinstance(fixture, dict):
            return _configuration_error("Fixture must contain a JSON object.")

    try:
        report = asyncio.run(
            verify_target(
                args.base_url,
                key_id=args.key_id,
                secret=secret,
                fixture=fixture,
                message=args.message,
                probe_keys=args.probe_keys,
                timeout_seconds=args.timeout,
            )
        )
    except KeyboardInterrupt:
        return 130

    _print_report(report, json_output=args.json_output)
    return 0 if report.passed else 1


def _print_report(report: ConformanceReport, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return

    status = "PASS" if report.passed else "FAIL"
    print(f"{status}  {report.base_url}")
    for check in report.checks:
        marker = "PASS" if check.passed else "FAIL"
        print(f"  [{marker}] {check.name}: {check.detail}")


def _configuration_error(message: str) -> int:
    print(f"augmentworks-target: error: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
