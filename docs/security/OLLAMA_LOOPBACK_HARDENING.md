# Ollama loopback boundary: hardening notes

## Implemented guardrails

- Only HTTP(S) URLs with literal loopback IP addresses are accepted.
- The exact hostname `localhost` is normalized to `127.0.0.1` before the request URL is built. The HTTP client therefore does not rely on DNS or hosts-file resolution for this name.
- Hostnames such as `localhost.evil`, `localhost.`, `localhost.localdomain`, DNS aliases for loopback, and IPv4-mapped IPv6 literals are rejected.
- Invalid ports, URL credentials, paths, queries, and fragments are rejected.
- Redirects are not followed and environment-configured proxies are disabled.
- Response bodies are capped at 8 MiB before JSON parsing; malformed UTF-8 and JSON errors are surfaced as `OllamaError`.
- Existing timeout behavior and regression tests for redirects, proxies, and response size remain in place.
- Raw socket/OS errors during response reads are normalized to bounded, retryable `OllamaError` values, so a mid-response disconnect does not escape the health/error-handling boundary.

## Validation coverage

The policy tests cover IPv4/IPv6 loopback literals, exact-localhost normalization, deceptive hostname forms, invalid ports, URL credentials, non-base URLs, redirect attempts, proxy isolation, oversized responses, and invalid UTF-8.

## Review status

This document records implementation hardening and test coverage only. It is **not an independent security assessment**. Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) must remain open until an independent reviewer evaluates the network boundary, records findings, and any required follow-up changes are completed.
