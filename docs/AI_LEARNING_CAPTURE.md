# AI Learning capture subsystem

Status: staged implementation; only features verified by code and tests should be considered available.

## Goal

The user creates a topic, reviews its outline and supervises collection of lessons. The user does not use this repository as a separate study product: Personal-AI-assistant may retrieve published knowledge when answering questions in its normal chat experience. Retrieval integration is optional, isolated and disabled by default.

## Target workflow

1. Create a course with topic, language, level, scope and provider.
2. Receive or import an outline response; retain raw response and provenance.
3. Before approval, allow the user to add, delete, edit and reorder topics.
4. Approve or reject topics individually or in a group. Only approved topics may receive lessons.
5. Send topics individually, selectively or in batches; record each run and its topic membership.
6. Split provider responses into lessons. Ambiguous mappings require manual review.
7. Pause, resume and retry independently at course, topic and lesson levels.
8. Edit content by creating a new version; retain source, raw response and content hash.
9. Review and explicitly publish content. Only published versions under approved topics may be retrieved.
10. Search internal knowledge from the normal assistant response path only after separately enabling and testing the integration.

## Isolation and compatibility

- Code lives in app/ai_learning/ and must not rewrite app/learning.py or the existing chat model.
- A separate database, data/ai_learning.db, has its own checksummed migrations. The current learning database is not migrated or modified.
- AI_LEARNING_ENABLED=0 and AI_LEARNING_RETRIEVAL_ENABLED=0 are the defaults.
- Knowledge-store errors must not break the existing chat path.
- Lexical search is the initial retrieval implementation. Embeddings and semantic search are future optional additions.
- Model-generated and web-extracted text are untrusted data; embedded instructions never outrank system policy.

## Data model

The isolated schema contains courses, topics, lessons, content versions, runs, batch membership, sources, retrievable chunks and knowledge relations. Foreign keys, status constraints and indexes are versioned. The migration history is immutable; new schema changes require a new migration.

## Implementation boundary today

The initial stage includes isolated storage, course/topic APIs, topic ordering, approval/rejection, lesson creation, content versioning, manual review/publishing, chunking and lexical search. A user-confirmed Chrome extension now inserts prompts into the existing ChatGPT UI and captures the latest visible assistant response when the user explicitly requests it. It does not click Send automatically and depends on the provider's current DOM. Durable background queue/pause-resume, resilient response parsing, embeddings and retrieval in the main chat answer path are not yet implemented.

## Acceptance criteria

- With the feature flag off, the module database is not initialized and the module API is disabled.
- Unknown databases are never reset or automatically adopted.
- Unapproved topics cannot create lessons; unreviewed content cannot be published.
- Search returns only active chunks from published versions and includes provenance IDs.
- Unit and integration tests run without internet or a live model.
- The existing chat path remains disconnected until an explicit, tested integration is implemented.
