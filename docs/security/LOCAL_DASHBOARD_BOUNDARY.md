# Local dashboard network boundary

## Threat

The application intentionally exposes an unauthenticated local dashboard and API on loopback. Checking only the TCP peer address is insufficient against DNS rebinding: a browser can resolve an attacker-controlled hostname to a loopback address while sending that hostname in the HTTP `Host` header.

## Enforced boundary

- The request peer must be a loopback IP literal.
- The HTTP `Host` hostname must be exactly `localhost`, `127.0.0.1`, or `::1`.
- Forwarded-IP headers are not used to establish the peer identity.
- The synthetic `testserver` exception is accepted only when the ASGI server scope itself reports the TestClient-only `testserver` tuple; a real TCP server reports its bound IP address.
- Regression tests reject attacker-controlled Host values, including `localhost.evil` and rebinding-style hostnames, while retaining the supported loopback forms.

## Limits

This is a loopback/Host boundary, not authentication. PR #646 proposes an additional API-origin boundary: validate `Origin` exactly against the request scheme/host/port and use `Sec-Fetch-Site` to reject cross-site and same-site cross-origin browser requests to `/api/`, while preserving non-browser local clients that omit both headers. The policy does not apply to ordinary dashboard navigation or static assets. The implementation and tests are pending exact-head CI; see [ADR-018](../architecture/ADR-018-same-origin-local-api-validation.md). Keep the server bound to loopback; do not expose it through a LAN interface, reverse proxy, tunnel, or public endpoint. Independent security review tracked by [issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) remains required.
