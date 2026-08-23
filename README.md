# AugmentWorks Target

Python SDK and language-neutral protocol for connecting a customer-hosted AI
application to AugmentWorks behavioral evaluations.

> **Experimental (`aw-target/0.1`)** — the wire protocol and Python API may
> change before `1.0`. Use it only with synthetic data in a dedicated test
> environment. Do not send production
> data, customer transcripts, PHI, PCI data, regulated financial records,
> production credentials, or raw model chain-of-thought.

## The reverse-API model

A normal SDK calls an API owned by its vendor. AugmentWorks works in the other
direction: you host a small **target adapter** next to your application, and the
AugmentWorks runner calls that adapter during an evaluation.

```text
private AugmentWorks                         your environment

industry packet -> evaluation runner  --->  target adapter -> your chatbot
 scenarios          orchestrates HTTP        (this SDK)       tools + sandbox
 assertions          signs requests                 |
 evidence            records results          observable state
```

The adapter translates four stable protocol operations into whatever functions
your chatbot already exposes. Your application can use any model provider,
agent framework, message shape, or internal tool API. AugmentWorks does **not**
need your model-provider keys and does not require every customer chatbot to
implement the same OpenAPI schema.

The lifecycle is deliberately small:

| Operation | Endpoint | Adapter responsibility |
|---|---|---|
| `prepare` | `POST /v1/attempts` | Create isolated synthetic state for one scenario repetition. |
| `send` | `POST /v1/attempts/{attempt_id}/turns` | Deliver a synthetic user turn and return visible output plus structured observable events. |
| `observe` | `POST /v1/attempts/{attempt_id}/observe` | Read allow-listed state from the relevant system of record. |
| `cleanup` | `DELETE /v1/attempts/{attempt_id}` | Remove temporary state, safely and idempotently. |

Discovery is available at
`GET /.well-known/augmentworks-target.json`. The complete language-neutral
contract is in
[`spec/target-v0.1.openapi.json`](https://github.com/jeffskafi/augmentworks-target/blob/v0.1.0/spec/target-v0.1.openapi.json).

## Installation

Install the core SDK and conformance CLI from PyPI:

```bash
python -m pip install "augmentworks-target==0.1.0"
```

FastAPI adapters should install the optional integration:

```bash
python -m pip install "augmentworks-target[fastapi]==0.1.0" "uvicorn[standard]>=0.30,<1"
```

## Five-minute FastAPI target

Requires Python 3.10 or newer. Install the FastAPI integration and a local ASGI
server:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install "augmentworks-target[fastapi]==0.1.0" "uvicorn[standard]>=0.30,<1"
export AUGMENTWORKS_TARGET_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

Create `app.py`:

```python
import os

from fastapi import FastAPI

from augmentworks_target import Target
from augmentworks_target.fastapi import create_router


# Replace this in-memory dictionary with an isolated test tenant, sandbox API,
# or fixture store. It must never point at production for v0.1 evaluations.
attempts: dict[str, dict] = {}


async def prepare(request):
    attempts[request.attempt_id] = {
        "order": dict(request.fixture.get("order", {})),
        "refunded": False,
    }
    return {
        "protocol_version": request.protocol_version,
        "status": "ready",
        "attempt_id": request.attempt_id,
    }


async def send(attempt_id, request):
    state = attempts[attempt_id]
    wants_refund = "refund" in request.message.content.lower()
    if wants_refund:
        state["refunded"] = True
        state["order"]["status"] = "refunded"
        state["order"]["refunded_amount"] = state["order"].get("amount", 0)

    return {
        "protocol_version": request.protocol_version,
        "turn_id": request.turn_id,
        "message": {
            "role": "assistant",
            "content": (
                "The synthetic order was refunded."
                if wants_refund
                else "How can I help with the synthetic order?"
            ),
            "finish_reason": "stop",
        },
        "events": [],
        "finished": wants_refund,
    }


async def observe(attempt_id, request):
    state = attempts[attempt_id]
    observable = {
        "order.status": state["order"].get("status"),
        "order.refunded_amount": state["order"].get("refunded_amount", 0),
    }
    return {
        "protocol_version": request.protocol_version,
        "request_id": request.request_id,
        "observations": [
            {
                "key": key,
                "value": observable.get(key),
                "source": "synthetic-order-store",
                "authoritative": True,
            }
            for key in request.probe_keys
        ],
    }


async def cleanup(attempt_id):
    attempts.pop(attempt_id, None)


target = Target(
    name="refund-sandbox",
    version="2026-08-20",
    prepare=prepare,
    send=send,
    observe=observe,
    cleanup=cleanup,
)

app = FastAPI()
app.include_router(
    create_router(
        target,
        secrets={"local-eval": os.environ["AUGMENTWORKS_TARGET_SECRET"]},
    )
)
```

Run it locally:

```bash
uvicorn app:app --reload
curl http://127.0.0.1:8000/.well-known/augmentworks-target.json
```

The callbacks may be synchronous or asynchronous. They may also return the
corresponding Pydantic response model instead of a dictionary. See
[`examples/refund_agent_fastapi`](https://github.com/jeffskafi/augmentworks-target/tree/v0.1.0/examples/refund_agent_fastapi) for a complete
synthetic adapter with structured tool events.

`Target` infers `observation` and `cleanup` only when their callbacks are
actually configured. Tool-event support cannot be determined safely from a
function signature, so it is fail-closed by default. If `send` can return
`ToolCallEvent` or `ToolResultEvent`, declare that explicitly:

```python
from augmentworks_target import TargetCapabilities

target = Target(
    name="refund-sandbox",
    version="2026-08-20",
    prepare=prepare,
    send=send,
    observe=observe,
    cleanup=cleanup,
    capabilities=TargetCapabilities(tool_events=True),
)
```

Explicit `observation` or `cleanup` values must agree with the corresponding
callbacks; contradictory configuration fails during startup. An adapter with
no cleanup callback advertises `cleanup: false`, and its cleanup endpoint
returns `cleanup_not_supported` instead of a false HTTP 204 success.

## Check protocol conformance

The CLI checks discovery compatibility and (when a fixture is supplied) the
authenticated prepare/send/observe/cleanup lifecycle with correlated IDs. Keep
secrets in an environment variable rather than shell history:

The bundled repository example includes a synthetic fixture. From a checkout
of the `v0.1.0` tag, run:

```bash
augmentworks-target verify http://127.0.0.1:8000 \
  --key-id local-eval \
  --secret-env AUGMENTWORKS_TARGET_SECRET \
  --fixture examples/refund_agent_fastapi/fixture.json \
  --message "Please refund order SYNTH-1001"
```

Use the conformance check in CI against a short-lived test deployment before
providing its HTTPS base URL and key ID to AugmentWorks.

## Authentication and security posture

Discovery is public and contains no secret material. Every lifecycle request is
authenticated with four headers:

```text
AW-Key-Id: customer-key-identifier
AW-Timestamp: 1787289600
AW-Nonce: unique-random-value
AW-Signature: v1=<64 lowercase hex characters>
```

The `v1` signature is HMAC-SHA256 over this canonical UTF-8 string:

```text
<unix timestamp>
<nonce>
<UPPERCASE METHOD>
<path and query exactly as sent>
<lowercase SHA-256 hex of exact request-body bytes>
```

Join the five lines with one LF (`\n`) and no trailing newline.

Always serve the adapter over TLS outside local development. Store the shared
secret in a secret manager, scope it to one environment, rotate it, and never
log it. Targets should use a constant-time comparison, reject timestamps outside
the configured skew window (five minutes by default), and reject a nonce seen
within that window. The included in-memory nonce store is for a single-process
development server; a multi-worker or production-like deployment needs a shared
atomic store.

The adapter is an intentionally narrow test surface:

- Give it synthetic fixtures, test accounts, and allow-listed sandbox tools.
- Do not put model-provider keys in AugmentWorks. They remain inside your
  application environment if your test deployment needs them.
- Return externally visible assistant output and structured tool/result events,
  not hidden reasoning, raw chain-of-thought, internal prompts, or unrelated
  logs.
- Expose only the observation probes required by the packet. Prefer the
  authoritative sandbox database or tool state over asking the model what it
  believes happened.
- Do not connect this experimental release to production or regulated data. A
  future production-data mode would require separate contractual, privacy,
  retention, access-control, and compliance work.

HMAC authenticates who sent a request and protects its bytes from alteration.
It does not make an unsafe sandbox safe, prove an evaluation is complete, or
turn AugmentWorks output into certification.

## Idempotency and ambiguous timeouts

Transport replay protection and application idempotency solve different
problems. A retry uses a **new timestamp and nonce** but the **same application
idempotency key**.

- Cache the result of `prepare` and `send` by idempotency key for the attempt.
- Return the cached result when the same key and same payload are retried.
- Return a conflict when a key is reused with a different payload.
- Never create a new turn ID or idempotency key merely because `send` timed out.
  The chatbot or a tool may already have completed the action. Query `observe`
  first; retry the identical turn only when the observed state makes that safe.
- Make `cleanup` idempotent. It is valid to repeat cleanup after a timeout, and
  an already-absent attempt is a successful cleanup.

These rules matter most for consequential actions such as synthetic refunds,
messages, account changes, approvals, or transfers.

## Repository boundary

This public repository contains only the customer integration contract:

- the language-neutral OpenAPI protocol;
- typed Python request/response models;
- HMAC signing and verification;
- the FastAPI router;
- the conformance CLI; and
- synthetic examples and tests.

The proprietary AugmentWorks service remains separate. Industry packets,
scenario content, assertions, orchestration, scoring, evidence generation, and
the customer portal belong to the private AugmentWorks runner. A packet tells
the runner *what behavior to test*; this SDK tells the runner *how to reach the
customer's application*. No packet logic or buyer-specific evidence is shipped
to the target adapter.

## Status and roadmap

`0.1.x` is intended to validate the smallest useful integration with initial
design partners. Near-term work is intentionally demand-driven:

1. Stabilize the prepare/send/observe/cleanup contract through real synthetic
   support-agent and refund evaluations.
2. Add durable nonce-store examples, key-rotation guidance, and hosted
   conformance diagnostics.
3. Add framework helpers only where repeated integrations justify them; the
   core protocol will remain independent of model and agent vendors.
4. Add another language SDK (likely TypeScript) when customer codebases require
   it rather than attempting feature parity prematurely.
5. Maintain signed PyPI releases and define a compatibility/deprecation policy
   after the protocol has survived pilot integrations.

Please open an issue for a protocol ambiguity or integration blocker. Do not
submit real customer data, credentials, prompts, or proprietary evaluation
artifacts in public issues.

## License

Apache-2.0. See
[`LICENSE`](https://github.com/jeffskafi/augmentworks-target/blob/v0.1.0/LICENSE).
