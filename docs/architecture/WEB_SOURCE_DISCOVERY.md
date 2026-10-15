# Automatic web-source discovery for learning jobs

## Scope

When a learning job has no user-provided source text, the worker can search a public HTML search endpoint, fetch a small number of readable pages, and use their bounded text as context for lesson generation. User-provided text takes precedence and is not silently replaced.

## Network safety and limits

- Only absolute HTTP(S) URLs are accepted; embedded credentials and non-standard ports are rejected.
- localhost, private, loopback, link-local, and other non-global IP destinations are rejected. Redirect targets are validated again.
- Proxy environment variables are ignored for this adapter so they cannot silently change the destination policy.
- Requests have a timeout, a response byte limit, a small source count, and per-page/combined text bounds.
- Only HTML, XHTML, and plain-text content types are accepted; script, style, navigation, and other irrelevant page elements are omitted.
- Failure or empty results are recorded as a web-search evidence event; learning can fall back to model knowledge with an explicit unverified status.

## Provenance

For successful retrieval, the local learning database stores the exact bounded context, the source URL, a title-bearing content hash, and a note that the page has not been independently verified. The model output and retrieved text are not automatically promoted to verified knowledge. A content hash proves identity of the stored excerpt, not the factual correctness of the page.

## Current limitations

This is a first provider adapter, not a multi-provider search planner. It does not yet compare independent sources, infer technology versions comprehensively, adjudicate conflicts, or establish licensing/authority. Public availability is not permission to republish a full source. Cloud-compute discovery and scheduling is a separate roadmap capability and is not implemented by this module.