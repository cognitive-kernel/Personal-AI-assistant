# AI Learning module

This package is optional and isolated. It must not replace the existing chat model, current learning module, or task runtime.

## Feature gates

- AI_LEARNING_ENABLED=0 (default): management API is disabled; startup does not initialize the module database.
- AI_LEARNING_ENABLED=1: enables the management API and initializes only AI_LEARNING_DB_PATH (default data/ai_learning.db).
- AI_LEARNING_RETRIEVAL_ENABLED=1: additionally enables lexical search over explicitly published content. It has no effect unless the module itself is enabled.

Do not expose the host service beyond loopback. The host application intentionally has no remote authentication.

## Implemented

Isolated SQLite schema and checksummed migrations, course/topic management, explicit topic approval/rejection, topic ordering, lesson/version creation, review-before-publish, chunking, lexical retrieval over published content only, an optional fail-open retrieval hook in the existing chat path, persistent global pause/resume, per-topic pause/resume, per-lesson review-workflow pause/resume, and workflow-stage progress indicators. The chat hook is disabled by default.

Progress percentages describe **content workflow completion**, not a verified measurement of model mastery: 0% pending, 10% active capture, 25% lesson record created, 75% content captured and awaiting review, 90% reviewed, and 100% published.

## Not implemented yet

A durable job queue with exact browser-session continuation, robust DOM-independent provider parsing, semantic embeddings, query feedback and broader quality evaluation. Retrieval traces store a one-way hash of the query and the IDs of retrieved chunks, not the raw query text. The Chrome extension opens ChatGPT, inserts and attempts to send the prompt after one explicit Start action, then captures the stable answer. Successful outline capture deduplicates topic titles, approves pending topics for unattended progression, and starts lesson batches for approved topics that do not already have lessons. Lesson batches continue automatically after successful capture. Lesson content remains in needs_review and is never automatically published. The primary dashboard workflow now uses a local Playwright worker with a dedicated persistent Chrome profile, so the Chrome extension is not required to request outlines or lessons. After `python run.py` creates `.venv`, stop the server and install the optional browser extra into that managed environment with `.venv\\Scripts\\python.exe -m pip install -e ".[browser]"` on Windows (or `.venv/bin/python -m pip install -e ".[browser]"` on Linux/macOS); the worker uses the installed Chrome executable and only interacts with the visible ChatGPT page. Set `AI_LEARNING_CHROME_PATH` if Chrome is installed in a nonstandard location and `AI_LEARNING_BROWSER_PROFILE` to customize the isolated browser profile directory. The older extension remains available as a fallback, but it is no longer the dashboard refresh path. The Playwright worker is DOM-sensitive and is not a durable queue. Content is still unreviewed until explicitly reviewed and published. The chat hook does not change the configured model; it adds bounded internal context only when both feature flags are enabled and matching published knowledge exists.

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
- After a power loss, restart the application and use **ادامه / بازیابی پس از قطعی برق** in the dashboard. A persisted in-flight run is retried from its saved prompt; if no run remains in flight, the workflow continues with approved topics that do not yet have lessons.
- Completed outline/topic/lesson/version transactions remain stored. An answer that ChatGPT generated but that was not committed to SQLite may need to be requested again. A repeated outline import deduplicates existing topic titles; lessons are only considered complete after their content transaction commits.
- Pause is cooperative, not an emergency kill: the current browser action may finish before the worker reaches a safe pause point. The existing per-run cancel action remains separate and terminal.
