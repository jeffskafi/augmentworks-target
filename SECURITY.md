# Security policy

## Supported versions

Only the latest `0.1.x` release receives security fixes while the target
protocol is experimental. Upgrade to the newest patch release before reporting
an issue.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
private **Report a vulnerability** form under the GitHub Security tab. If that
form is unavailable, email `hello@augmentworks.ai` with the subject
`augmentworks-target security report`.

Include the affected version, impact, minimal reproduction, and any proposed
mitigation. Do not include real customer data, production credentials, model
provider keys, shared HMAC secrets, private prompts, or regulated data.

## Security scope

Version `0.1.x` is synthetic-only and is intended for isolated test systems.
It is not approved for production transcripts, PHI, PCI data, regulated
financial records, or production tool credentials. HMAC request authentication
does not make an unsafe target environment safe; deployments remain
responsible for TLS, secret storage, nonce replay protection, idempotency,
least-privilege sandboxing, and cleanup.
