# ADR-018: Same-origin validation for the unauthenticated local API

- Status: Proposed
- Date: 2026-10-31
- Owners: Personal AI Assistant maintainers

## Context

The dashboard intentionally has no login because it is a local-first application bound to loopback. The existing peer-IP and Host checks mitigate remote access and DNS rebinding, but they do not stop a hostile website from submitting a simple cross-origin form to a loopback URL. Several state-changing API endpoints accept bodyless POST requests; the AI-learning reset endpoint can delete captured learning data when browser work is quiescent.

## Decision

- Keep the existing loopback-peer and exact-local-Host checks.
- For every `/api/` request, reject browser Fetch Metadata values other than `same-origin`; allow `none` only for safe navigation methods (GET, HEAD, OPTIONS). Reject `cross-site` and `same-site`.
- When an `Origin` header is present, require an exact match of scheme, hostname, and effective port with the request URL. Reject `null`, malformed origins, credentials, paths, queries, fragments, invalid ports, and origin mismatches.
- Preserve local non-browser clients that omit both Origin and Fetch Metadata headers. The existing loopback peer check still applies.
- Apply the policy to API routes, not ordinary dashboard navigation or static assets. Do not add permissive CORS behavior.
- Set `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` on all responses so an attacker cannot clickjack the local dashboard inside a cross-origin frame.

## Consequences

- Cross-origin HTML forms and browser fetches cannot invoke bodyless local API actions, including destructive learning-data reset.
- Same-origin dashboard requests continue to work. Local CLI/test clients without browser metadata remain supported.
- This is CSRF defense in depth, not authentication. A local process or malicious local software can still access the API; the app must remain bound to loopback.
- Independent security review tracked by [issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) remains required.

## Validation

Regression tests cover cross-site POST reset attempts, same-site but cross-origin requests, `Origin: null`, cross-site API reads, same-origin browser POSTs, non-browser local POSTs, and ordinary dashboard navigation. Exact-head CI is required before this ADR is accepted.
