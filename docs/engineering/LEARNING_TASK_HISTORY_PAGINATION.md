# Learning task audit history pagination

## Finding

The learning-course history panel requested at most 100 task audit events and ignored the API cursor (has_more / next_before_id). The task API already supported cursor pagination, and the general task manager already exposed older event pages; the course-specific history panel did not.

## Change

- Retain the existing chronological display of the newest event page.
- Add an explicit older-events action when the API reports another page.
- Request older pages with the returned before_id cursor and prepend them, preserving chronological order.
- Keep existing event nodes when a later page request fails; report the failure through a polite live region.

## Verification and limits

A regression guard checks cursor construction, continuation state, and chronological prepend behavior. Existing task API and browser task-history pagination coverage remains applicable to the server contract. Exact-head CI is required before merge. No local or human tests were run; no database schema, event persistence, or task state behavior changed.
