# Local dashboard network boundary

## Threat

The application intentionally exposes an unauthenticated local dashboard and API on loopback. Checking only the TCP peer address is insufficient against DNS rebinding: a browser can resolve an attacker-controlled hostname to a loopback address while sending that hostname in the HTTP `Host` header.

## Enforced boundary

- The request peer must be a loopback IP literal.
- The HTTP `Host` hostname must be exactly `localhost`, `127.0.0.1`, or `::1`.
- Forwarded-IP headers are not used to establish the peer identity.
- The synthetic `testclient`/`testserver` exception exists only for the in-process ASGI test transport; a real TCP peer cannot select `request.client.host`.
- Regression tests reject attacker-controlled Host values, including `localhost.evil` and rebinding-style hostnames, while retaining the supported loopback forms.

## Limits

This is a loopback/Host boundary, not authentication and not a complete CSRF defense. Keep the server bound to loopback; do not expose it through a LAN interface, reverse proxy, tunnel, or public endpoint. Do not treat this control as a substitute for the independent security review tracked by [issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52).
