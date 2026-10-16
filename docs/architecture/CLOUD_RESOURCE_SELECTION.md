# Provider-neutral cloud resource selection

`app/cloud_resource_planner.py` produces a dry-run selection plan from discovered candidates. It is deliberately separate from provider-specific clients and task execution.

## Inputs

- A candidate with current source hash, reviewed source status, freshness, availability, terms URL, policy quota, provider domain, and observed claims.
- Resource requirements: minimum CPU count, minimum memory in GiB, optional GPU requirement/model allowlist, and optional provider-domain allowlist.

## Evidence rules

Only claims with `is_current=1`, `review_status=accepted`, and `source_content_sha256` equal to the candidate's current `content_sha256` can satisfy resource requirements. Memory values must have explicit supported units (`GiB`, `GB`, `MiB`, or `MB`); decimal GB is converted conservatively to GiB. Unknown or malformed capacity is not guessed.

The existing provider-neutral policy preflight must return `ready_for_provider_adapter`; this is a prerequisite, not a proof that the resource is physically available now, truly zero-cost, or authorized for execution.

## Output and limits

The planner reports the candidate recommendation, normalized capacity, evidence/policy blockers, and reasons for rejecting other candidates. Its output always contains `execution_authorized=false` and `remote_execution_enabled=false`.

This is not a scheduler and does not enqueue tasks, contact provider APIs, access credentials, create accounts, provision machines, or send user data. A later API can expose the dry-run result; any provider execution requires a separate adapter, explicit authorization, contract tests, local checkpointing, quota enforcement, and failover/recovery tests.


## Local dry-run API

The loopback-only endpoint `POST /api/cloud-resources/plan` accepts a JSON object with `min_cpu_count`, `min_memory_gib`, `require_gpu`, `gpu_models`, and `allowed_provider_domains`. It evaluates stored candidates and returns the plan, per-candidate reasons, and policy review needs. It does not enqueue a task or call a provider client. Invalid numeric requirements return HTTP 422; non-loopback requests are rejected by the shared router dependency.


## UI

The local page includes a dry-run form for minimum CPU and memory, optional GPU/model requirements, and an optional provider-domain allowlist. Results are rendered as text, including per-candidate reasons. The form invokes only the loopback planning endpoint and cannot start remote execution.
