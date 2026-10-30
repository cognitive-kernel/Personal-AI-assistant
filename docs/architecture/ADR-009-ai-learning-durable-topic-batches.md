# ADR-009: Durable per-topic runs for selected AI-learning batches

- Status: Accepted
- Date: 2026-10-30
- Scope: `app/ai_learning/browser_automation.py`, `app/ai_learning/service.py`

## Context

The selected-topic browser workflow created a durable run for the first topic but kept the remaining selected topic IDs in the worker thread's in-memory follow-up argument. A process restart could recover the first run from SQLite while losing the rest of the user's explicit selection. Dashboard resume could later rediscover pending approved topics, but that required a separate manual action and could expand beyond the original selection.

## Decision

- Persist one run per selected topic in SQLite before dispatching the first browser request.
- Give each topic run a request key derived from the caller's request key and topic ID, preserving idempotency at the topic boundary.
- Use the existing global FIFO dispatcher for the first run and let each worker finalizer dispatch the next persisted run.
- Keep prompts bounded to one topic and preserve per-topic pause, cancellation, retry and content-completion semantics. Pausing an active topic signals its worker and requeues the run instead of marking it terminally cancelled. Lesson completion checks the paused state inside the same SQLite write transaction so a response racing with pause cannot create a lesson.
- Manual continuation and recovery of legacy lesson runs materialize remaining approved topics as durable SQLite runs before starting the worker, but only when no runnable same-course queue already exists. Lesson workers do not create new in-memory follow-up batches; this prevents lost in-memory work and duplicate generation.
- Keep the existing single-process browser-worker boundary; this decision does not add cross-process leases or claim multi-instance safety.

## Validation

- Regression test verifies that a selected multi-topic batch creates one durable run per topic.
- Restart regressions simulate loss of in-memory worker state for new selected batches, manual continuation, and legacy in-flight runs; remaining topic runs must stay queued.
- Pause regressions verify queued and active paused topics are not dispatched, active work is requeued rather than terminally cancelled, a concurrent lesson completion cannot save while paused, direct start is rejected, and unpausing resumes FIFO dispatch.
- Existing queue, cancellation, retry, pause and browser tests remain required in CI.

## Consequences

A process restart no longer loses selected topics that had not yet started. The queue can resume them in FIFO order without creating new work or changing already completed lesson content. Exact continuation of a remote ChatGPT generation remains impossible to guarantee; the saved prompt may be submitted again. The AI-learning subsystem remains intended for one local application process until a separate lease/heartbeat design is implemented and independently tested.
