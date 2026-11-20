# ADR 0001 — Feature-owned dashboard modules and versioned UI contracts

- **Status:** Proposed; implementation is in draft PR #795 and has not been merged.
- **Date:** 2026-11-20
- **Decision owners:** Repository maintainers and project owner.

## Context

The dashboard's inline script accumulated unrelated chat, knowledge, storage, task, cloud-account, settings, learning, health, theme, and navigation workflows. Those workflows shared page-level helpers and mutable state. A failure or stale response in one feature could affect unrelated UI initialization, and module ownership was not enforceable. The AI-learning UI also had two competing page implementations and route definitions: the feature-owned `app/ai_learning/ui.py` route served `app/ai_learning/static/index.html`, while a later route in `app/main.py` pointed at a legacy `app/static/ai-learning.html`. The active `app/ai_learning/static/app.js` still coordinates course, topic, lesson/version, run-history, search and global-control workflows.

The project owner clarified that the application and databases are still raw development scaffolding, no module is considered complete and usable, and there is no operational user dataset produced by completed modules. This is an owner-reported product-state statement, not evidence that every database file is empty. It removes hypothetical production-data compatibility as a reason to avoid architectural correction, but does not relax migration or test-fixture safety.

## Decision

1. Each dashboard capability owns its event handlers, refresh lifecycle, request sequencing, and mutable UI state in a dedicated feature module.
2. Cross-feature shared DOM/API primitives are provided by the immutable `dashboardCore v1` contract. Consumers validate the version and required DOM controls before binding.
3. Learning submodules use explicit, versioned contracts: `learningCore v1`, `learningLessons v1`, and `learningOverview v1`. The path controller must not load before its dependencies.
4. The settings feature exposes only `window.dashboardSettings.refresh()` through a versioned contract to the page shell.
5. Stale read responses must not overwrite newer UI state. Storage mutations are serialized within the storage feature module. Server-side concurrency and durable task locks remain authoritative; browser guards are not substitutes for them.
6. Modules render untrusted content using DOM text APIs, not `innerHTML`.
7. Architecture fitness tests enforce script order, dependency versions, state ownership, safe rendering, and the module inventory. Browser tests cover user-visible failure and concurrency behavior.
8. The autonomous engineering console is split into a shared transport/DOM core and independent runs, status, controls, and progress modules. They communicate through versioned contracts and narrow custom events; the orchestrator only validates module contracts and schedules independent refreshes. The active `app/ai_learning/static/app.js` and the learning path controller remain in the modularity backlog for further cohesion review. The shadow `app/static/ai-learning.*` implementation and duplicate `/ai-learning` route are removed; the feature router is the only page owner.

## Consequences

- A feature can fail its own initialization without silently taking ownership of unrelated feature state.
- Script order and exported contracts become explicit dependencies that tests can verify.
- The number of files increases, but each file has a bounded responsibility and a visible dependency boundary.
- Changes to shared contracts require coordinated updates and compatibility tests.
- The learning-candidate feature is still being extracted in PR #797. PR #795 and PR #797 share the same byte-identical `dashboard-core.js`; merge/rebase order must be coordinated.

## Validation and rollout

- Draft PR: [#795](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/795).
- Companion evidence-preservation PR: [#797](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/797).
- Exact-head GitHub CI and browser E2E are required before the implementation is considered validated.
- Target-host validation remains mandatory before merge. No live database was accessed or modified by this ADR or the associated refactor.
- Do not merge until every exact-head workflow and required target-host check is green.
