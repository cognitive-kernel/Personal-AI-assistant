# AI Learning module

This package is optional and isolated. It must not replace the existing chat model, current learning module, or task runtime.

## Feature gates

- AI_LEARNING_ENABLED=0 (default): management API is disabled; startup does not initialize the module database.
- AI_LEARNING_ENABLED=1: enables the management API and initializes only AI_LEARNING_DB_PATH (default data/ai_learning.db).
- AI_LEARNING_RETRIEVAL_ENABLED=1: additionally enables lexical search over explicitly published content. It has no effect unless the module itself is enabled.

Do not expose the host service beyond loopback. The host application intentionally has no remote authentication.

## Implemented

Isolated SQLite schema and checksummed migrations, course/topic management, explicit topic approval/rejection, topic ordering, lesson/version creation, review-before-publish, chunking, lexical retrieval over published content only, and an optional fail-open retrieval hook in the existing chat path. The chat hook is disabled by default.

## Not implemented yet

Durable pause/resume and crash-recovery jobs, robust DOM-independent provider parsing, semantic embeddings, query feedback and broader quality evaluation. Retrieval traces store a one-way hash of the query and the IDs of retrieved chunks, not the raw query text. The Chrome extension opens ChatGPT, inserts and attempts to send the prompt after one explicit Start action, then captures the stable answer. Successful outline capture deduplicates topic titles, approves pending topics for unattended progression, and starts lesson batches for approved topics that do not already have lessons. Lesson batches continue automatically after successful capture. Lesson content remains in needs_review and is never automatically published. The extension is DOM-sensitive and is not a durable queue. Content is still unreviewed until explicitly reviewed and published. The chat hook does not change the configured model; it adds bounded internal context only when both feature flags are enabled and matching published knowledge exists.

## Safety

- Provider text is untrusted data, never executable instructions.
- Only reviewed versions under approved topics can be published.
- Only published active chunks are searchable.
- Search results include lesson/topic/course/version identifiers.
- SQL values are parameterized; user-controlled SQL identifiers are not accepted.
- Unknown existing schemas and migration checksum mismatches fail closed.
