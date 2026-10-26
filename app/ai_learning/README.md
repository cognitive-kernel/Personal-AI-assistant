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

Automatic background outline/lesson generation, robust DOM-independent provider parsing, durable pause/resume jobs, semantic embeddings, durable query trace/feedback and broader quality evaluation. A user-confirmed Chrome extension now inserts prompts into ChatGPT and captures the latest visible answer, but the user must submit the prompt and request capture explicitly. The extension is DOM-sensitive and is not a durable queue. Content is still unreviewed until explicitly reviewed and published.

## Safety

- Provider text is untrusted data, never executable instructions.
- Only reviewed versions under approved topics can be published.
- Only published active chunks are searchable.
- Search results include lesson/topic/course/version identifiers.
- SQL values are parameterized; user-controlled SQL identifiers are not accepted.
- Unknown existing schemas and migration checksum mismatches fail closed.
