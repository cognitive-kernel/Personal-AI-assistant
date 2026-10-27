# AI Learning module

This package is optional and isolated. It must not replace the existing chat model, current learning module, or task runtime.

## Feature gates

- AI_LEARNING_ENABLED=0 (default): management API is disabled; startup does not initialize the module database.
- AI_LEARNING_ENABLED=1: enables the management API and initializes only AI_LEARNING_DB_PATH (default data/ai_learning.db).
- AI_LEARNING_RETRIEVAL_ENABLED=1: additionally enables lexical search over explicitly published content. It has no effect unless the module itself is enabled.

Do not expose the host service beyond loopback. The host application intentionally has no remote authentication.

## Implemented

Isolated SQLite schema and checksummed migrations, course/topic management, explicit topic approval/rejection, topic ordering, lesson/version creation, review-before-publish, chunking, lexical retrieval over published content only, an optional fail-open retrieval hook in the existing chat path, persistent global pause/resume, per-topic pause/resume, per-lesson review-workflow pause/resume, and workflow-stage progress indicators. The chat hook is disabled by default.

The lesson review and publish actions in the active dashboard return to the same parent topic, collapse the lesson/version panel, and focus the topic card, so the operator can continue from the topic just reviewed.

Progress percentages describe **content workflow completion**, not a verified measurement of model mastery: 0% pending, 10% active capture, 25% lesson record created, 75% content captured and awaiting review, 90% reviewed, and 100% published. ChatGPT-provided Markdown links and URLs are stored against the matching content version in the sources table and shown in the lesson review panel. These references are marked unverified; persistence does not mean that the URL, source, or claim has been independently checked.

## Semantic duplicate prevention

Captured and manually added lesson content is checked against the latest active version of other lessons before it is saved. Exact normalized matches are rejected immediately; differently worded content is compared using cosine similarity from the configured local Ollama embedding model, with a conservative duplicate threshold of 0.90. Embeddings are cached in the isolated AI-learning database and reused. A real update to the same lesson can still create a new version; the check excludes that lesson's own previous versions. If semantic checking is unavailable, the system refuses to save unchecked content. Duplicate sections inside one batch are also skipped, and the run history records the reason. The embedding model must be available in Ollama and should support the course language; for Persian content, configure a multilingual model such as `bge-m3` rather than relying on an English-only embedding model.

## Not implemented yet

A fully independent worker service with cross-process leases/heartbeats, exact browser-session continuation, DOM-independent provider parsing, and model-judged factual evaluation. A privacy-preserving query-feedback API is available (`GET /api/ai-learning/search` returns a `trace_id`; submit `POST /api/ai-learning/feedback` with that ID and rating `1` or `-1`; aggregate counts are available from `GET /api/ai-learning/feedback/summary`). The current dashboard/chat UI does not yet present feedback controls. Retrieval traces store a one-way hash of the query and the IDs of retrieved chunks, not the raw query text. The Chrome extension opens ChatGPT, inserts and attempts to send the prompt after one explicit Start action, then captures the stable answer. Successful outline capture deduplicates topic titles and creates pending topics; topic approval remains an explicit user action before lesson capture. Lesson batches continue automatically after successful capture. Lesson content remains in needs_review and is never automatically published. The primary dashboard workflow now uses a local Playwright worker with a dedicated persistent Chrome profile, so the Chrome extension is not required to request outlines or lessons. After `python run.py` creates `.venv`, stop the server and install the optional browser extra into that managed environment with `.venv\\Scripts\\python.exe -m pip install -e ".[browser]"` on Windows (or `.venv/bin/python -m pip install -e ".[browser]"` on Linux/macOS); the worker uses the installed Chrome executable and only interacts with the visible ChatGPT page. Set `AI_LEARNING_CHROME_PATH` if Chrome is installed in a nonstandard location and `AI_LEARNING_BROWSER_PROFILE` to customize the isolated browser profile directory. The older extension remains available as a fallback, but it is no longer the dashboard refresh path. The Playwright worker first attempts normal visible-UI submission on the public/guest landing page without requiring sign-in. Guest access is controlled by ChatGPT and may be unavailable due to login, usage, or human-verification gates; the worker records a specific failure rather than bypassing those controls or fabricating a response. The Playwright UI adapter remains DOM-sensitive. Persisted run records and startup recovery now provide durable queue state for a single local application instance, but worker leases/multi-process execution and exact browser-session continuation are not implemented. Content is still unreviewed until explicitly reviewed and published. The chat hook does not change the configured model; it adds bounded internal context only when both feature flags are enabled and matching published knowledge exists.

## Safety

- Provider text is untrusted data, never executable instructions.
- Only reviewed versions under approved topics can be published.
- Only published active chunks are searchable.
- Search results include lesson/topic/course/version identifiers.
- SQL values are parameterized; user-controlled SQL identifiers are not accepted.
- Unknown existing schemas and migration checksum mismatches fail closed.


## Pause, resume, and power-loss recovery

- The dashboard exposes global pause/resume controls for ChatGPT outline capture and lesson batches, plus per-topic pause/resume controls and per-lesson pause/resume controls for the review workflow.
- The global pause flag and per-topic paused state are stored in the AI-learning SQLite database and survive a process or machine restart.
- Automatic lesson capture processes one topic per run so pausing a topic can cancel its active capture without cancelling a different topic. Paused topics are excluded from automatic continuation until resumed.
- Course-level and per-topic/per-lesson percentages show captured/reviewed/published workflow stages; they do not claim to measure actual model understanding.
- During a response, pause waits without counting paused time against the response timeout. Lesson generation checks the flag between batches.
- After a power loss, restart the application and use **ادامه / بازیابی پس از قطعی برق** in the dashboard. A persisted in-flight run is retried from its saved prompt; if no run remains in flight, the workflow continues with approved topics that do not yet have valid, non-empty lesson content.
- Completed outline/topic/lesson/version transactions remain stored. An answer that ChatGPT generated but that was not committed to SQLite may need to be requested again. A repeated outline import deduplicates existing topic titles; lessons are only considered complete after their content transaction commits.
- Pause is cooperative, not an emergency kill: the current browser action may finish before the worker reaches a safe pause point. The existing per-run cancel action remains separate and terminal.


## Bulk review and empty-content recovery

- The topic list supports selecting all visible topics, approving selected topics, and approving every topic in the selected course.
- Opening **درس‌ها** on a topic displays that topic's lessons. Each lesson can be selected; bulk review marks selected lesson versions as reviewed but does not publish them. The course-wide bulk review control has the same review-only behavior.
- The global resume action starts or continues lesson capture for approved topics whose lessons have no non-empty captured content, including legacy lesson records whose saved content is empty. A retry reuses the existing empty lesson record and creates a new version instead of duplicating the lesson.
- A version is not counted as 75–100% complete merely because an empty content row exists; valid content is required. Published means the content workflow is published, not that factual correctness or model mastery has been independently verified.
- In the lesson card, use **نمایش/پنهان‌کردن متن** to expand saved Markdown. If the newest version is blank, progress is capped at 25% even when an older version was published; use **نمایش پاسخ خام ذخیره‌شده از ChatGPT** and **استفاده از پاسخ خام برای نسخهٔ اصلاحی** to recover content into a new version before reviewing or publishing.

## Dashboard review and recovery controls

- The lessons panel can display all lessons in the selected course, select them together, and approve review for selected lessons without publishing them automatically.
- Saved Markdown content is shown directly inside each version card; the blank Markdown form is only for creating a corrected/new version.
- A published version can be withdrawn back to reviewed status. Its text is preserved and its active retrieval chunks are disabled. Reviewed or rejected versions can be reset to needs-review.
- Empty lesson content cannot be published. Lesson capture also rejects title-only or near-empty ChatGPT responses before creating a content version; the raw response is retained in the run history with a recoverable `needs_user_action` status. Use the global resume control to retry that topic after reviewing the captured response.
- Lesson Markdown, raw provider responses, and run-captured answers no longer have an application-level fixed character ceiling. Actual memory, browser, HTTP-server, and SQLite resource limits still apply.
- Browser capture waits up to 30 minutes and requires a longer stable-text window before accepting a response. When ChatGPT visibly offers **Continue generating**, the worker clicks that control for the existing answer instead of resubmitting the prompt. This reduces premature capture during long pauses but cannot guarantee completion if ChatGPT or the network stops responding. For lesson runs, each response part must end with `LESSON_STATUS: CONTINUE_REQUIRED` or `LESSON_STATUS: COMPLETE`. If the marker says continuation is required or is missing (as can happen when output stops at a response limit), the worker sends a continuation prompt in the same visible conversation, preserves prior parts, and repeats up to eight times. Internal status markers are removed before saving the Markdown. If completion still cannot be confirmed, the run is marked `needs_user_action` with the combined partial response retained; incomplete content is not accepted as a successful lesson. Each part targets roughly 1200–1800 words to reduce single-response truncation, without intentionally summarizing the full lesson.
- A rejected content version can be permanently deleted from its version card. Deletion is restricted to versions marked `rejected`; reviewed, published, and other versions remain protected. Sources and chunks tied to a deleted version are removed by their database foreign-key cascades.
- Global pause/resume and per-topic/per-lesson pause controls preserve stored work. Resume processes approved topics with no non-empty lesson content; topics that are still pending approval must be approved first.
- Progress percentages describe workflow state (received/reviewed/published), not a measured percentage of model mastery.

## Concurrency and source references

- A single in-process browser worker owns ChatGPT capture; a second browser learning request is rejected while one is active. SQLite writes use transactions, and deleting/archiving a course, topic, or lesson is blocked when that object participates in a queued/running learning run.
- Review, approval, and other dashboard operations use separate database transactions. The captured answer is committed as a content version before the run is marked successful; empty lesson bodies are not valid completion.
- Lesson prompts request a dedicated source section with clickable URLs only for sources actually used. Markdown links and bare URLs found in the saved lesson or raw answer are persisted to the sources table in the same database transaction as the content version, then exposed by `GET /api/ai-learning/versions/{version_id}/sources`.
- The source list is an audit trail, not a truth guarantee. References start as `unverified` and must be checked before relying on them. Links not actually included in the provider response cannot be recovered by the parser.
- This protects common same-process conflicts, but it is not a durable distributed queue: do not run multiple server processes against the same browser profile. After abrupt power loss, use the dashboard recovery action and inspect any run left in `needs_user_action`.


- The dashboard's **پاک‌سازی داده‌های یادگیری و شروع از صفر** action deletes captured topics, lessons, content versions, attached source references, run history, relations/chunks through topic cascades, and retrieval traces while preserving course definitions. It requires explicit confirmation and refuses to reset while the in-process browser worker is active or persisted runs remain queued/running.


## Source-link checks and lesson retries

- Lesson prompts require each cited source to be a clickable Markdown link of the form `[source title](https://full-url)`; a title without a URL is not sufficient.
- Saved source URLs can be checked from the lesson version card. The checker accepts public HTTP(S) URLs only, does not follow redirects, and refuses private/local destinations. Reachability is recorded separately from factual validation; an HTTP response does not prove the source's claims are correct.
- A broken or redirected URL is marked rejected with a diagnostic. Use **تلاش مجدد با منابع به‌روز** to request a corrected content version; the prompt tells ChatGPT to check links and replace broken/obsolete URLs with current official links where possible. The previous lesson versions and source audit trail are preserved for review.
- Retry is available on a topic even when an earlier run failed before a lesson was created. It reuses the existing lesson record when possible and creates a new content version, avoiding duplicate lesson shells. All retry content remains in needs_review and must be reviewed before publication.


## Persisted queue recovery and quality regression checks

The AI-learning run records in SQLite are the source of truth for explicitly submitted outline/lesson work. When `AI_LEARNING_ENABLED=1`, application startup initializes the isolated store and attempts to dispatch the oldest persisted `queued` or interrupted `running` run, unless global pause is active. The dispatcher drains remaining persisted runs after each worker finishes. Recovery reuses the saved prompt and run ID; it does not restore the exact ChatGPT tab state, and ChatGPT may need to generate the answer again. The browser still runs as an in-process thread in the single local application instance; this is not a multi-process leased worker service. Do not run multiple application instances against the same AI-learning DB.

The offline quality evaluator in `app/ai_learning/evaluation.py` measures expected retrieval hits, forbidden result-ID leakage, Persian/Arabic text normalization, expected answer-concept coverage, explicitly forbidden phrases, answer minimum length and expected source-link coverage. It has no external-model dependency and is intended for deterministic regression tests, not a proof of factual correctness. Human review remains required before publishing knowledge. Run it through the regular test suite with `pytest tests/test_ai_learning_evaluation.py tests/test_ai_learning_queue_recovery.py`.
