# ADR-006: Durable per-topic runs for selected AI-learning batches

- Status: Accepted
- Date: 2026-10-30
- Scope: `app/ai_learning/browser_automation.py`, `app/ai_learning/service.py`

## Context

The selected-topic browser workflow created a durable run for the first topic but kept the remaining selected topic IDs in the worker thread's in-memory follow-up argument. A process restart could recover the first run from SQLite while losing the rest of the user's explicit selection. Dashboard resume could later rediscover pending approved topics, but that required a separate manual action and could expand beyond the original selection.

## Decision

- Persist one run per selected topic in SQLite before dispatching the first browser request.
- Give each topic run a request key derived from the caller's request key and topic ID, preserving idempotency at the topic boundary.
- Use the existing global FIFO dispatcher for the first run and let each worker finalizer dispatch the next persisted run.
- Keep prompts bounded to one topic and preserve per-topic pause, cancellation, retry and content-completion semantics.
- When manually resuming a legacy lesson run, continue the old in-memory follow-up behavior only if no queued run remains for that course. This avoids generating a duplicate lesson for a topic that is already represented by a durable queued run.
- Keep the existing single-process browser-worker boundary; this decision does not add cross-process leases or claim multi-instance safety.

## Validation

- Regression test verifies that a selected multi-topic batch creates one durable run per topic.
- Restart regression simulates loss of in-memory worker state and verifies that the active run is recovered while all remaining topic runs stay queued.
- Existing queue, cancellation, retry, pause and browser tests remain required in CI.

## Consequences

A process restart no longer loses selected topics that had not yet started. The queue can resume them in FIFO order without creating new work or changing already completed lesson content. Exact continuation of a remote ChatGPT generation remains impossible to guarantee; the saved prompt may be submitted again. The AI-learning subsystem remains intended for one local application process until a separate lease/heartbeat design is implemented and independently tested.
