# Outbound network boundary for AI reviewers and source checks

## Threat and previous behavior

External reviewers use an explicit HTTPS base URL, an exact hostname allowlist, and explicit data-sharing approval. AI-learning source validation performs outbound HTTP(S) requests to saved public citations, and the separate knowledge-base indexer fetches pages from a strict HTTPS host allowlist. Every path must use the exact public IP that was validated; checking DNS and then letting a separate HTTP client resolve again creates a DNS-rebinding time-of-check/time-of-use gap.

## Enforced request path

- External reviewer configuration accepts HTTPS only, port 443 only, an exact normalized host allowlist match, no URL credentials, query, or fragment, and a versioned API path.
- AI-learning source checks accept HTTP on port 80 or HTTPS on port 443 only; they reject malformed ports, URL credentials, local hostnames, and non-public destinations.
- Knowledge-base ingestion accepts HTTPS only from `learn.microsoft.com`, `www.w3schools.com`, and `microsoft.github.io`, rejects credentials and custom ports, and caps URLs at 2048 characters.
- All DNS answers are inspected; if any answer is not a globally routable IP, the request is rejected.
- The first validated IP is pinned as the TCP destination for that request. The transport does not resolve the hostname a second time.
- The original hostname remains in the request URL and is used for TLS SNI and certificate hostname verification. HTTP source checks also pin the TCP destination, though they do not provide TLS confidentiality.
- Ambient proxy configuration is disabled.
- Redirects are not followed on the external-reviewer and AI-learning citation paths. Knowledge-base ingestion allows at most three redirects, but validates each target against its strict HTTPS hostname allowlist and pins the target IP before connecting; no credentials are attached to these GET requests.
- The response body is read with a 64 KiB hard limit before JSON parsing.
- The provider must still return a model identifier and system fingerprint for the result to be attributable.

## Regression coverage

`tests/test_learning_review_adapters.py` covers explicit data-sharing approval, malformed origins and ports, private DNS answers, pinned-IP transport, no proxy, no redirects, bounded response reads, and model-fingerprint validation. `tests/test_ai_learning_source_safety.py` covers pinned HTTP/HTTPS source checks, mixed public/private DNS answers, invalid ports, and redirect/proxy blocking. `tests/test_pinned_http.py` covers TCP IP pinning while retaining TLS hostname verification. `tests/test_knowledge_base.py` verifies explicit proxy isolation, exactly one DNS resolution at the pinned transport boundary, bounded URLs, and the allowlisted redirect policy.

## Remaining security gate

These controls reduce DNS-rebinding and redirect risks; they do not constitute an independent security assessment. Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) remains open until an independent reviewer assesses the complete network boundary and records any required follow-up.
