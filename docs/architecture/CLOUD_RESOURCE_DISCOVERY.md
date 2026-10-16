# Autonomous cloud compute discovery — first implementation boundary

## What it does

The application schedules a durable discovery task on startup and chains the next run for approximately 24 hours later. The worker searches a bounded set of general queries for public cloud compute options and stores source-backed candidates in a dedicated local SQLite database at `CLOUD_RESOURCES_DB_PATH` (default: `data/cloud_resources.db`).

The local API is loopback-only:

- `GET /api/cloud-resources?limit=100` lists recent candidates and the latest discovery run.
- `POST /api/cloud-resources/discover` queues a manual discovery task and returns HTTP 202.
- `PATCH /api/cloud-resources/{id}/review` records an explicit local review only if the submitted content hash still matches; approving a candidate requires an HTTPS terms URL.

Each candidate records its HTTPS source URL, hostname, title, SHA-256 of the page title plus fetched text, a short evidence excerpt, the discovery query, and keyword signals such as GPU, CPU, free-tier/credits, quota, terms, notebook, VM/container, and storage mentions. These signals describe what the page mentions; they do not establish that a service is free, available, suitable, or authorized for use.

## Trust and safety boundary

- Search results and page content are untrusted data. They are never interpreted as instructions or executed.
- Only HTTPS source URLs are persisted. The existing web-source adapter validates public destinations, redirects, content type, timeouts, and response size.
- New candidates start as `review_status=unreviewed` and `availability_status=unknown`. Quotas and terms remain unknown unless separately reviewed and recorded.
- The content hash covers the page title plus fetched text. If either changes, the review status is reset to unreviewed and previous availability/quota/terms claims are cleared. Re-seeing identical evidence preserves the review.
- Candidate evidence not seen for 30 days becomes stale and its availability is reset to unknown. Stale evidence cannot be approved until a new discovery refreshes it.
- A source review is not the same as provider eligibility: no remote execution should be allowed without fresh evidence, explicit eligibility, valid authorization, and a provider adapter that enforces quotas and terms.
- No account creation, credential handling, provisioning, job execution on remote hosts, quota bypass, or provider terms acceptance is implemented here.
- Durable source metadata and discovery history remain local. The provider catalog is stored in its own versioned database because it has a distinct operational lifecycle and review policy; this is a data-domain boundary, not a separate server requirement.

## Current limitations and next stage

This is autonomous **discovery**, not autonomous compute provisioning. The first version uses three bounded search templates and records candidate evidence; it does not reliably parse contractual free-tier limits, authenticate providers, reserve capacity, or dispatch jobs. It must not claim a resource is usable merely because a page mentions free compute.

Next stages:

1. Parse provider terms, quotas, region, runtime limits, expiry, authentication, and costs into explicit evidence-backed fields.
2. Add freshness checks and mark stale candidate metadata as unknown.
3. Add provider-specific adapters only where the provider's public API and terms permit them.
4. Compare local and provider capabilities, and schedule remote work only after a provider is explicitly reviewed/eligible and credentials/permissions are valid.
5. Keep all durable learning records, queue state, and checkpoints local; send only minimal, approved job inputs to remote compute.
6. Add UI controls for discovery history, candidate review, stale data, and provider eligibility.

No resource is automatically selected for execution by this implementation.


## Review and freshness workflow

Candidates are initially `unreviewed`, `unknown`, and `fresh` only because the page was just observed. The local reviewer can submit the current `content_sha256` to `PATCH /api/cloud-resources/{id}/review`; the server rejects stale hashes with HTTP 409. Approval requires an HTTPS `terms_url`, records `reviewed_at` and `reviewed_content_sha256`, and stores explicitly supplied quota/availability values. A page refresh with the same hash preserves review metadata; a changed title or text invalidates it.

After 30 days without re-observation, `freshness_status` becomes `stale` and `availability_status` is reset to `unknown`. This is a conservative discovery/review workflow, not yet a scheduler eligibility contract. Remote jobs must remain disabled until provider-specific terms, quota, credentials, and capability checks are implemented.
