# ADR-005: Global durable queue recovery for browser-based AI learning

- Status: Accepted
- Date: 2026-10-29
- Scope: `app/ai_learning/service.py`, `app/ai_learning/browser_automation.py`

## Context

The learning dashboard intentionally limits course listings to 100 rows and run-history listings to 50 rows per course. Reusing those presentation APIs for worker scheduling meant an older queued or interrupted run could become invisible to recovery after the limits were exceeded. A process restart could then leave that run stuck while newer work continued.

## Decision

- Queue scheduling and startup recovery query the durable SQLite store directly; dashboard pagination is not part of the queue contract.
- Startup recovery enumerates every `running` run belonging to a non-archived course and requeues it through the existing state-transition function.
- The dispatcher selects the globally oldest `queued` run by `created_at`, then ID as a stable tie-breaker.
- `needs_user_action`, `succeeded`, `failed`, and `cancelled` runs are not automatically resurrected by the dispatcher. A durable global pause prevents recovery.
- Tests cover a stale run outside the latest 50 runs of its course and queued work belonging to a course outside the dashboard's latest 100 courses.

## Consequences and limitations

This fixes a pagination-induced starvation/recovery defect without changing dashboard limits or creating new work. The browser worker remains single-instance and DOM-dependent; this decision does not provide cross-process leases, guarantee exact continuation of a remote ChatGPT generation, or replace target-host restart/load acceptance. Explicit user pause/cancel remains authoritative.
