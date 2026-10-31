# Cloud resource source URL validation

## Boundary and purpose

The manual cloud-resource candidate endpoint accepts a user-supplied source URL as unreviewed metadata. Adding a candidate does not fetch the URL and does not establish that the provider, offer, or source is trustworthy.

The URL boundary is deliberately strict so malformed authority syntax is rejected before it can be persisted and later presented for review.

## Accepted URL contract

- The scheme must be exactly HTTPS.
- A hostname must be present.
- User information is forbidden, including syntactically present empty username or password components.
- The port must be omitted or explicitly be the standard HTTPS port, 443.
- An explicit empty port delimiter (for example, `https://example.org:/path`) is invalid.
- Malformed authority/IPv6 syntax and invalid port syntax are rejected as a domain validation error.
- URL fragments are removed during normalization; the normalized hostname is lowercased and has a trailing dot removed.

The API maps invalid source URLs to HTTP 422. Duplicate normalized candidate URLs continue to map to HTTP 409.

## Trust and network boundary

A successfully added manual candidate remains unreviewed. URL normalization is not source verification, DNS validation, authorization, or evidence of factual correctness. Any later live fetch must continue through the public-source fetch boundary in `app/web_sources.py`, which applies its own URL, DNS-pinning, redirect, TLS, proxy, timeout, and response-size controls.

## Regression coverage

- `tests/test_cloud_resources.py` verifies rejection of empty userinfo, explicit empty ports, nonnumeric ports, and malformed bracketed IPv6 authorities at the normalization boundary.
- `tests/test_cloud_resource_routes.py` verifies the manual API returns HTTP 422 for malformed authorities and does not persist rejected candidates; valid entries and duplicate conflict behavior remain covered.

No database schema or migration is required.
