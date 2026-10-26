# AI Learning module

This package is optional and isolated. It must not replace the existing chat model, current learning module, or task runtime.

## Feature gates

- AI_LEARNING_ENABLED=0 (default): management API is disabled; startup does not initialize the module database.
- AI_LEARNING_ENABLED=1: enables the management API and initializes only AI_LEARNING_DB_PATH (default data/ai_learning.db).
- AI_LEARNING_RETRIEVAL_ENABLED=1: additionally enables lexical search over explicitly published content. It has no effect unless the module itself is enabled.

Do not expose the host service beyond loopback. The host application intentionally has no remote authentication.

## Implemented

Isolated SQLite schema and checksummed migrations, course/topic management, explicit topic approval/rejection, topic ordering, lesson/version creation, review-before-publish, chunking, and lexical retrieval over published content only.

## Not implemented yet

Chrome extension/browser automation, automatic outline/lesson generation, robust provider-response parsing, durable pause/resume jobs, semantic embeddings, answer synthesis, and the opt-in hook into the existing chat response pipeline. These are not simulated. The current content intake is manual until browser automation is separately reviewed and tested.

## Safety

- Provider text is untrusted data, never executable instructions.
- Only reviewed versions under approved topics can be published.
- Only published active chunks are searchable.
- Search results include lesson/topic/course/version identifiers.
- SQL values are parameterized; user-controlled SQL identifiers are not accepted.
- Unknown existing schemas and migration checksum mismatches fail closed.
