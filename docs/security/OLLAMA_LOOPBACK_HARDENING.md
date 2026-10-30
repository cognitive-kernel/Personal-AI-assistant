# Ollama loopback boundary: hardening notes

## Implemented guardrails

- Only HTTP(S) URLs with literal loopback IP addresses are accepted.
- The exact hostname `localhost` is normalized to `127.0.0.1` before the request URL is built. The HTTP client therefore does not rely on DNS or hosts-file resolution for this name.
- Hostnames such as `localhost.evil`, `localhost.`, `localhost.localdomain`, DNS aliases for loopback, and IPv4-mapped IPv6 literals are rejected.
- Invalid ports, URL credentials, paths, queries, and fragments are rejected.
- Redirects are not followed and environment-configured proxies are disabled.
- Response bodies are capped at 8 MiB before JSON parsing; malformed UTF-8 and JSON errors are surfaced as `OllamaError`.
- Existing timeout behavior and regression tests for redirects, proxies, and response size remain in place.
- Raw socket/OS and HTTP protocol errors during response reads (including incomplete bodies) are normalized to bounded, retryable `OllamaError` values, so a mid-response failure does not escape the health/error-handling boundary.

## Validation coverage

The policy tests cover IPv4/IPv6 loopback literals, exact-localhost normalization, deceptive hostname forms, invalid ports, URL credentials, non-base URLs, redirect attempts, proxy isolation, oversized responses, and invalid UTF-8.

## Review status

This document records implementation hardening and test coverage only. It is **not an independent security assessment**. Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) must remain open until an independent reviewer evaluates the network boundary, records findings, and any required follow-up changes are completed.


## Settings API validation parity

The `PATCH /api/settings` route uses the same pure `ollama.normalize_base_url` policy as the request client. This keeps configuration-time validation aligned with runtime enforcement: only HTTP(S) loopback endpoints without credentials, paths, query strings, fragments, invalid ports, or scoped IPv6 identifiers are accepted. Exact `localhost` is canonicalized to `127.0.0.1`; HTTPS loopback is supported consistently with the Ollama client policy. Validation is performed before persistence and makes no network request.
