# Task audit event display correction

## Finding

The learning-history panel fetched durable task audit events but rendered every non-status event using `new_status`. As a result, `priority_changed` and `scheduled` events displayed the unchanged task status instead of the changed priority or schedule. The database trigger already records old/new priority and schedule values; persistence was not the defect.

## Change

- Centralize event presentation in `formatTaskAuditEventChange`.
- Render status transitions, priority transitions, schedule transitions, and task creation from their corresponding audit fields.
- Preserve a fallback for unknown event types.
- Add a regression guard for each supported event type.

## Verification and limits

The regression test is part of the repository's automated test suite and must pass on the exact pull-request head before merge. No local or human tests were run. The change affects presentation only; it does not alter task state, event persistence, database schema, or task APIs.
