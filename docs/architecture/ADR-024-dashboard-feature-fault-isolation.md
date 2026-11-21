# ADR-024 — Dashboard feature fault isolation and local primitive fallback

- Status: Proposed; exact-head browser and full-suite validation pending.
- Date: 2026-11-21
- Scope: Browser dashboard feature scripts and their shared primitive contracts.
- Depends on: ADR-0001 dashboard feature module contracts.

## Context

The dashboard had extracted most feature logic from `index.html`, but almost every feature still failed closed when `dashboard-core.js` did not load. The inline learning-candidate feature also remained inside a 125-line inline script and directly destructured the shared global. Therefore the shared primitive asset remained a single failure point even though feature logic was split into files. The learning path modules had a similar dependency chain: a failure in `learningCore`, `learningLessons`, or `learningOverview` prevented the main path UI from initializing.

## Decision

1. Keep `dashboardCore v1` as a small, frozen, non-replaceable shared kernel for the normal path.
2. Each feature script includes a local fallback for only the generic primitives it consumes: safe DOM lookup, JSON HTTP request, and text-node append. If the shared kernel asset fails, the feature remains able to initialize without depending on another feature's implementation.
3. The learning-candidate workflow is a standalone `learning-candidates.js` feature module; no application feature logic remains in an inline page script.
4. Learning lessons and overview modules have local learning-core fallbacks. The learning path orchestrator treats lesson and overview modules as optional capabilities: one module failure does not prevent the rest of the path UI from initializing.
5. Each autonomous-engineering submodule has its own fallback for the small `autonomousEngineeringCore v1` transport/DOM contract. The orchestrator reports missing capabilities without disabling successfully loaded siblings.
6. Feature-owned state, refresh sequencing, event listeners, and domain operations remain in their own module. Fallbacks do not contain domain logic.
7. The task dashboard is split by responsibility into a view module (summary/filter/selection), a card module (rendering/history/actions), and a small orchestrator (refresh sequencing/polling/event wiring). Their frozen versioned contracts are validated before use.
8. Experience auto-capture consent is a separate module from experience-memory listing/search/curation, with independent state and local transport fallback.
9. Cloud resource candidate rendering/evidence review is a separate factory module from discovery orchestration, so list-card failures do not stop manual submission, discovery task polling, or planning.

## Invariants

- Failure to fetch `dashboard-core.js` must not prevent chat, task, storage, settings, learning, or autonomous-engineering feature scripts from registering their own UI.
- A failure in one learning submodule must not stop the path orchestrator or unrelated learning panels.
- The shared kernel cannot be silently overwritten by another script.
- No module uses unsafe `innerHTML` for user- or API-provided content.
- Task UI view/card module failures are contained to the task feature and do not stop sibling dashboard features.
- Fallback code is limited to generic transport/DOM primitives; feature state and behavior remain feature-owned.

## Validation

- Static architecture tests verify the inline feature script was removed and every feature has a local fallback.
- Browser E2E aborts the `dashboard-core.js` asset request and verifies feature globals and key controls still initialize without page errors tied to shared contracts.
- Full suite, dashboard smoke, CodeQL, and dependency audit must pass on the exact PR head.

## Residual scope

This change validates shared-core failure isolation. It does not prove every individual API endpoint or every feature action works end-to-end. Remaining feature files must continue to be split only where a distinct responsibility and contract can be identified; file-size thresholds alone are not acceptance evidence.
