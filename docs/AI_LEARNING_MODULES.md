# AI-learning browser module boundaries

## Entry point and failure isolation

`app/ai_learning/static/index.html` loads only `app.js` as a native ES module. The entry point dynamically imports the core and each feature module with `Promise.allSettled`; feature modules do not statically import one another. A failed download, syntax error, version mismatch or factory failure is recorded for that feature and does not reject the other imports.

The core module exports `createCore()` under contract version 1. If `core.js` itself cannot load or create the contract, the small fallback implementation in `app.js` provides the same API/DOM helper contract. This prevents the shared helper file from becoming a single point of failure.

Each feature module exports a version constant and a factory. The factory receives the core contract plus only the dependencies it needs. No feature module reaches into another module's lexical state or imports its implementation directly.

## Ownership

| File | Owns | Private state / refresh lifecycle |
|---|---|---|
| `core.js` | API transport, DOM helpers, form serialization, status reporting, progress meter and guarded action wrapper | No feature state |
| `courses.js` | Course CRUD, topic tree, hierarchy/reordering, topic selection and course progress rendering | Course identity, topics, selected topic IDs, catalog/detail/progress request generations |
| `runs.js` | Run history, run detail, cancellation and deletion | Run-history request generation |
| `lessons.js` | Lesson lists, content capture, version review/publish, source checks, bulk quality checks and deleted-lesson recovery | Per-render-root load generation and course-wide lesson-view state |
| `controls.js` | Global pause/resume, worker recovery state and destructive reset | Global-control request generation |
| `search.js` | Retrieval search and result feedback | Search request generation |
| `app.js` | Module loading, version checks, dependency injection, fallback behavior and independent polling | No course, lesson, run, search or control state |

## Dependency contracts

- `courses.js` receives `setRunHistoryLoader()` and `setLessonLoader()` callbacks from the entry point. This avoids importing runs or lessons into the course module.
- `runs.js` receives course identity, current-course validation and course reload functions.
- `lessons.js` receives course identity/topics, course validation/progress/reload functions and run-history refresh.
- `controls.js` receives course identity, course reload and run-history refresh.
- `search.js` depends only on the core helper contract.
- These dependencies are injected by `app.js`; feature modules have no static imports between them and no circular import path.

All contracts are versioned at `v1`. A module factory validates the core contract before binding its controls. The entry point checks each feature contract before initialization.

## Request ownership and stale-response rules

| Feature | Request owner | Rule |
|---|---|---|
| Course catalog | `courses.js` | Only the newest catalog request may replace the list or its error state |
| Course detail | `courses.js` | Only the newest detail request for the selected course may render |
| Course progress | `courses.js` | Independent generation; a prior course's progress cannot repaint the current course |
| Run history | `runs.js` | Only the latest run-history request may render |
| Lesson content | `lessons.js` | A per-root generation and current-course check reject stale content and errors |
| Global controls | `controls.js` | Only the latest control-state read may render |
| Retrieval search | `search.js` | A late response or error from an older query is ignored |

A failure in run history does not fail course-detail rendering. The entry point starts course loading and global-control refresh independently; the five-second poll invokes each available refresh separately.

## Degraded-mode behavior

- If the shared core module fails, `app.js` uses its local fallback core and still initializes available features.
- If courses fail, course-owned forms and selectors are disabled; independently loaded modules remain initialized.
- If lessons fail, lesson-specific bulk controls are disabled and the course module's lesson-loader callback reports that the lesson module is unavailable.
- If run history fails, run-specific controls are disabled and course rendering continues without waiting for run history.
- If global controls fail, pause/resume/reset controls are disabled; course, lesson, run and search modules continue.
- If search fails, only the search submit control is disabled.
- Failed modules are named in the page status. They are not silently treated as successful.

## Route and asset security

`app/ai_learning/ui.py` is the sole owner of `/ai-learning` and the `/ai-learning-assets/{asset_name}` allowlist. The route retains its loopback dependency. The obsolete `app/static/ai-learning.*` page and duplicate route were removed; feature assets are served only from `app/ai_learning/static/`.

## Architecture fitness tests

- `tests/test_ai_learning_ui.py` checks route uniqueness, loopback dependency, the explicit asset allowlist, versioned factories, no static imports between feature modules, and file-size budgets.
- Feature modules are limited to 24,000 bytes each; the entry-point orchestrator is limited to 7,000 bytes.
- `tests/test_browser_e2e.py` verifies that a failed core module uses the fallback and that a failed lesson module does not block course and search UI.
- Existing Chromium tests cover stale course-list, same-course detail, run-history, search and reset responses.

Do not reintroduce feature business logic into `app.js`, static imports between feature modules, or a second `/ai-learning` page route. Extend a feature's own module and its injected contract instead.
