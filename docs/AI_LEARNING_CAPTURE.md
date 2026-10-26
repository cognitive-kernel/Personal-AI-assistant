# AI Learning capture subsystem

Status: initial implementation present on the feature branch; target-machine acceptance is still required. Only features verified by code and tests should be considered available.

## Goal

The user starts a course workflow once. The extension retrieves and stores the outline, skips existing topic titles, and continues through lesson batches automatically; lesson content still requires review before publication. The user does not use this repository as a separate study product: Personal-AI-assistant may retrieve published knowledge when answering questions in its normal chat experience. Retrieval integration is optional, isolated and disabled by default.

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

The initial stage includes isolated storage, a dedicated management page, course/topic APIs, topic ordering, approval/rejection, lesson creation and title editing, content versioning, manual review/publishing, chunking and lexical search. The Chrome extension opens the existing ChatGPT UI, inserts and attempts to send the prompt after one explicit Start action, and monitors for a changed stable assistant response before capturing it. Successful outline capture automatically advances to deduplicated lesson batches. Manual recovery is reserved for browser/UI/provider failures. Run history exposes ambiguous captures for editing and reprocessing. Durable queue/pause-resume, crash recovery, resilient provider-independent parsing, embeddings and detailed query trace/feedback are not yet implemented. The main chat path has an optional retrieval hook, disabled by default; with its feature flag off, the existing response path is unchanged. CI and target-machine checks are still required before operational acceptance.

## Acceptance criteria

- With the feature flag off, the module database is not initialized and the module API is disabled.
- Unknown databases are never reset or automatically adopted.
- Unapproved topics cannot create lessons; unreviewed content cannot be published.
- Search returns only active chunks from published versions and includes provenance IDs.
- Unit and integration tests run without internet or a live model.
- The existing chat path uses only the separately gated optional retrieval adapter; with both flags off, the previous path remains unchanged.
