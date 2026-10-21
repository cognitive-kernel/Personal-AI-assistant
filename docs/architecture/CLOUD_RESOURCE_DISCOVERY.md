# Autonomous cloud compute discovery — first implementation boundary

## What it does

The application schedules a durable discovery task on startup and chains the next run for approximately 24 hours later. The worker searches a bounded set of general queries for public cloud compute options and stores source-backed candidates in a dedicated local SQLite database at `CLOUD_RESOURCES_DB_PATH` (default: `data/cloud_resources.db`).

The local API is loopback-only and the main local web page now includes a cloud-resource discovery/review panel:

- `GET /api/cloud-resources?limit=100` lists recent candidates and the latest discovery run.
- `POST /api/cloud-resources/discover` queues a manual discovery task and returns HTTP 202.
- `POST /api/cloud-resources/manual` adds a user-entered candidate from an HTTPS URL, title, and evidence description; the entry always starts unreviewed and requires the same relevance checks as discovered candidates.
- `DELETE /api/cloud-resources/{id}` deletes a candidate and dependent claim rows only when no saved selection-plan decision references it; plan history is retained for auditability.
- `PATCH /api/cloud-resources/{id}/review` records an explicit local review only if the submitted content hash still matches; approving a candidate requires an HTTPS terms URL.
- `PATCH /api/cloud-resources/{candidate_id}/claims/{claim_id}/review` accepts/rejects a single extracted claim only when the claim is current and its source hash matches. Accepting a claim requires the parent source to be approved first.

The UI renders all page titles and excerpts with `textContent`, validates source links as HTTPS before opening them, and uses `noopener noreferrer` for new tabs. It never inserts source text as HTML.

Each candidate records its HTTPS source URL, hostname, title, SHA-256 of the page title plus fetched text, a short evidence excerpt, the discovery query, and keyword signals such as GPU, CPU, free-tier/credits, quota, terms, notebook, VM/container, and storage mentions. A generic regex adapter also extracts explicit textual claims such as vCPU counts, memory/storage sizes, GPU model names, session limits, monthly hours, credit amounts, price rates, and region codes. A source-bound Kaggle CLI documentation adapter recognizes only the official `Kaggle/kaggle-cli` kernels reference and extracts documented accelerator option names and CLI operations. These are documentation capabilities, not proof that a specific account can use an accelerator or that quotas are available. All extracted claims remain unreviewed.

## Trust and safety boundary

- Search results and page content are untrusted data. They are never interpreted as instructions or executed.
- Only HTTPS source URLs are persisted. The existing web-source adapter validates public destinations, redirects, content type, timeouts, and response size. It explicitly refuses automated page fetches from `kaggle.com` and its subdomains because the current [Kaggle Terms of Use](https://www.kaggle.com/terms) prohibit automated crawling/scraping of its services. The source-bound Kaggle adapter reads the official CLI documentation hosted on GitHub, not Kaggle web pages.
- New candidates start as `review_status=unreviewed` and `availability_status=unknown`. Quotas and terms remain unknown unless separately reviewed and recorded.
- The content hash covers the page title plus fetched text. If either changes, the review status is reset to unreviewed and previous availability/quota/terms claims are cleared. Re-seeing identical evidence preserves the review.
- Candidate evidence not seen for 30 days becomes stale and its availability is reset to unknown. Stale evidence cannot be approved until a new discovery refreshes it.
- Extracted claims are stored separately with the source content hash, adapter version, exact quote, and their own `unreviewed` status. When source content changes, previous claim rows remain as history with `is_current=0`; newly extracted claims are unreviewed. Candidate-level approval never auto-accepts individual claims.
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

After 30 days without re-observation, `freshness_status` becomes `stale` and `availability_status` is reset to `unknown`. This is a conservative discovery/review workflow, not yet a scheduler eligibility contract. Remote jobs must remain disabled until provider-specific terms, quota, credentials, and capability checks are implemented. Accepted claims are evidence inputs only; they are not an authorization or scheduling decision.


## Extracted claims are not verified limits

The generic adapter uses bounded regular expressions against the page title and normalized text. Each output claim includes a type, parsed value/unit, exact matched quote, adapter identifier, source content hash, extraction time, and an independent `review_status=unreviewed`. A missing match means “not extracted,” not “zero” or “unlimited.” A match proves only that the page text contains the quoted phrase; it does not prove that the offer is current, applicable to this account/region, or permitted for unattended workloads. The UI labels these claims as unverified. Each claim can be accepted or rejected independently; acceptance is blocked until the parent source is approved and does not imply that the provider is eligible for remote execution.


## Preflight eligibility assessment

`app/cloud_resource_policy.py` evaluates a candidate using explicit blockers and review-needed reasons. The current policy is deliberately strict: current source hash must be approved, evidence must be fresh, availability must be marked available, an HTTPS terms URL must exist, capacity/limit/free-tier claims must be accepted, `quota.max_cost_usd` must be numeric zero, and no positive price-rate claim may be accepted.

The highest possible result is `ready_for_provider_adapter`, not `eligible`. Every assessment includes `execution_authorized=false`. A provider-specific adapter must still enforce the terms, region, authentication, quotas, expiry, session limits, and zero-spend boundary before any remote work is considered. This assessment is a preflight checklist, not a guarantee that a provider will not charge or revoke access.



## First source-bound provider adapter: Kaggle CLI documentation

The source-bound adapter recognizes only HTTPS URLs on `github.com` whose path is an official `Kaggle/kaggle-cli` kernels documentation file. It extracts only explicit accelerator option names and documented CLI operations. It does not infer GPU entitlement, current quota, session allowance, price, region availability, or permission for unattended execution.

Official references reviewed for the adapter contract:

- Kaggle CLI kernel commands: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- Kaggle CLI authentication: https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/auth.md
- Kaggle CLI kernel metadata: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md

The official CLI documentation states that submitting a kernel can execute it, that credentials are required for push/status/output/log operations, and that some accelerator options may be restricted to particular competitions or administrators. Consequently, this adapter is extraction-only: it does not invoke the CLI, upload code, access credentials, create accounts, submit kernels, or authorize remote work. Account-specific quotas and terms remain unresolved until separately documented and reviewed. `execution_authorized` remains false.


## Kaggle quota snapshots: parser only, no credential access

The official CLI exposes a top-level `kaggle quota --csv` command for the authenticated account's weekly GPU and TPU usage. Its rows contain resource, used, remaining, total, and quota refresh time. This is account-specific, shared across the account's projects, and can change when another notebook runs; it must not be treated as a permanent public resource claim.

`app/kaggle_quota.py` parses bounded CSV output, rejects malformed/inconsistent values, requires an observation timestamp, and considers the snapshot stale after 15 minutes or at the next quota reset. `app/kaggle_quota_client.py` adds a read-only client that runs only the fixed `quota --csv` command when both a feature flag and explicit user-consent argument are true; it requires an absolute executable path, uses no shell, bounds execution time, passes only a small environment allowlist, and does not persist credentials, stdout, or stderr. The application does not call this client automatically yet. Any future dispatch must refresh quota and fail closed on missing/stale quota. Kaggle's current Terms of Use state that a user may not operate more than one active account and restrict automated scraping; account rotation and scraping are therefore not fallback strategies.

References:

- Official Kaggle CLI quota implementation: https://github.com/Kaggle/kaggle-cli/blob/main/src/kaggle/api/kaggle_api_extended.py
- Official Kaggle CLI kernel commands and credential requirements: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- Kaggle Terms of Use (active version listed as June 22, 2025): https://www.kaggle.com/terms

No provider job is submitted by this stage. The provider policy still returns `execution_authorized=false`.


## Explicitly consented read-only quota client

`fetch_quota_snapshot()` is a standalone building block, not an enabled background integration. It requires both `enabled=True` and `user_consent=True`, and an absolute path to the CLI executable. It executes only `quota --csv`; there is no command interpolation, shell, account creation, kernel push, upload, or deletion. Only the environment variables needed for process startup and Kaggle CLI credential discovery are passed to the child process. CLI error output is deliberately not propagated to avoid leaking account details or credentials.

The application startup and cloud scheduler do not call this client. A later UI/API integration must persist an auditable consent state, explain the account data accessed, and provide revocation before enabling periodic quota checks.


## Local opt-in and revocation workflow

The local UI now offers an optional Kaggle quota panel. The default state is disabled. Enabling it requires a checkbox plus a valid absolute path to the user's existing Kaggle CLI executable. The local API is loopback-only and records consent-granted/revoked events in the cloud-resource database with the consent-policy version; it does not store API tokens or raw CLI output.

Quota refresh is manual only: the user presses the refresh button, and the backend runs only the fixed read-only `quota --csv` command through the opt-in client. There is no startup check, periodic polling, notebook submission, upload, or remote execution. Parsed GPU/TPU quota values and observation/reset times are stored locally. Revocation disables access, clears the executable path and current quota snapshot, and retains the consent audit events.


## Second source-bound provider adapter: Oracle Cloud Always Free documentation

The Oracle adapter recognizes only the exact official OCI Always Free resources page on
`docs.oracle.com`. It extracts explicit documented shape names, CPU/memory limits,
monthly OCPU/GB-hour limits, the home-region constraint, temporary host-capacity
unavailability, and the warning that idle Always Free compute instances may be reclaimed.
All extracted claims remain unreviewed and bound to the source content; they do not
establish that a particular tenancy currently has capacity or is eligible.

Official references:

- Always Free resources and limits:
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Free Tier versus time-limited trial:
  https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm
- OCI REST API and required request signing:
  https://docs.oracle.com/en-us/iaas/Content/API/Concepts/usingapi.htm
- Required API signing keys and tenancy/user OCIDs:
  https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm

The published Always Free compute offer is CPU-based, not a GPU entitlement: the
documentation describes up to 2 OCPUs and 12 GB memory for Ampere A1 in the tenancy's
home region, subject to current quota and host capacity. Idle instances may be reclaimed.
OCI API operations require signed requests and account-specific tenancy/user credentials.
This adapter does not read credentials, create accounts, provision resources, launch jobs,
or authorize execution. The policy continues to return `execution_authorized=false`;
provider-specific terms, actual tenancy limits, budget enforcement, and remote-execution
consent remain mandatory before an execution adapter can be enabled.


## OCI account service-limit snapshots (read-only client)

`app/oci_quota.py` parses bounded JSON from the OCI Compute service-limit endpoint
into a short-lived, typed snapshot. It rejects duplicate limit/scope records,
non-finite or inconsistent numbers, malformed payloads, and oversized output.
`app/oci_quota_client.py` can invoke only the fixed read-only command
`oci limits value list --service-name compute`, using an explicit absolute CLI path,
config-file path, profile, region, and compartment/tenancy OCID. Both feature enablement
and explicit user consent are required; shell execution is disabled, the child receives
a minimal environment, and CLI stderr is not propagated.

This snapshot reports **service limits**, not Always Free eligibility, current physical
capacity, zero cost, or permission to provision an instance. A loopback-only API and UI now expose this client behind an explicit, revocable consent
record. The user manually triggers each refresh; startup and the task scheduler never
call it. Parsed numeric limits and scope metadata are stored in the local cloud-resource
database only while consent remains enabled. Raw CLI output, private keys, and credentials
are not stored. Revocation clears the snapshot and stored paths/identifiers. This remains
a read-only service-limit check, not an execution authorization.
