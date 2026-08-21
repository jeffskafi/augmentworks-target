# Synthetic refund target

This example wraps a deterministic, in-memory refund agent with the
AugmentWorks target protocol. It demonstrates the complete lifecycle:

1. prepare isolated state from a synthetic fixture;
2. send a user turn;
3. report a structured tool call and result;
4. observe allow-listed state; and
5. clean up the attempt.

It does **not** connect to a real payment service, customer database, or LLM.
Use synthetic data only.

## Run it

From the repository root, install the development dependencies and set a local
HMAC credential:

```bash
python -m pip install -e ".[dev]"
export AUGMENTWORKS_TARGET_KEY_ID="local-development"
export AUGMENTWORKS_TARGET_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m uvicorn examples.refund_agent_fastapi.app:app --port 8001
```

Keep that terminal running. In a second terminal, export the same secret and
run the conformance client:

```bash
export AUGMENTWORKS_TARGET_SECRET="paste-the-same-secret-here"
augmentworks-target verify http://127.0.0.1:8001 \
  --key-id local-development \
  --fixture examples/refund_agent_fastapi/fixture.json
```

The command exits with `0` only when discovery, prepare, send, observe, and
cleanup all pass. Omit `--fixture` to check only the public discovery document.

## Adapt it to your application

Keep the adapter next to your application and replace the body of `send` with
your normal chatbot entry point. The evaluator calls your adapter; you do not
give AugmentWorks your model-provider credentials. Return only user-visible
assistant output, structured tool boundaries, and explicitly allow-listed state
observations. Do not return chain-of-thought, secrets, stack traces, or arbitrary
database state.

The in-memory attempt and nonce stores are appropriate only for this local
example. A deployed multi-process target needs shared, expiring stores and TLS.
