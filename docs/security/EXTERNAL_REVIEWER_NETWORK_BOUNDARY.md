# External AI reviewer network boundary

## Threat and previous behavior

An external reviewer is configured with an explicit HTTPS base URL, an exact hostname allowlist, and explicit data-sharing approval. The previous transport checked that DNS answers were public and then delegated the request to an HTTP client that resolved the hostname again. A DNS answer could therefore change between validation and connection (DNS rebinding).

## Enforced request path

- Configuration accepts HTTPS only, port 443 only, an exact normalized host allowlist match, no URL credentials, query, or fragment, and a versioned API path.
- All DNS answers are inspected; if any answer is not a globally routable IP, the request is rejected.
- The first validated IP is pinned as the TCP destination for that request. The transport does not resolve the hostname a second time.
- The original hostname remains in the request URL and is used for TLS SNI and certificate hostname verification.
- Ambient proxy configuration is disabled.
- Redirects are not followed, so prompts and bearer credentials cannot be forwarded to another origin.
- The response body is read with a 64 KiB hard limit before JSON parsing.
- The provider must still return a model identifier and system fingerprint for the result to be attributable.

## Regression coverage

`tests/test_learning_review_adapters.py` covers explicit data-sharing approval, malformed origins and ports, private DNS answers, pinned-IP transport, no proxy, no redirects, bounded response reads, and model-fingerprint validation. `tests/test_pinned_http.py` covers TCP IP pinning while retaining TLS hostname verification.

## Remaining security gate

These controls reduce DNS-rebinding and redirect risks; they do not constitute an independent security assessment. Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) remains open until an independent reviewer assesses the complete network boundary and records any required follow-up.
