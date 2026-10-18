# UI acceptance criteria

This document defines the acceptance contract for the local-first operations dashboard. A visual refresh is not complete until the functional and accessibility checks below pass.

## Design principles

- Persian-first, right-to-left layout with correct direction for English text and mixed-language content.
- Responsive from narrow mobile viewports through desktop; no horizontal overflow for forms, status output, or task cards.
- Local-first: no remote fonts, CDN scripts, analytics, telemetry, or externally hosted UI dependencies.
- Semantic HTML, keyboard-operable controls, visible focus, meaningful accessible names, readable contrast, and reduced-motion support.
- Consistent visual tokens for spacing, typography, borders, surfaces, status colors, focus, and elevation.
- Clear loading, empty, success, validation, permission, network, and server-error states; never turn an error into a misleading empty state.
- Destructive or irreversible actions require explicit confirmation and explain scope; storage health checks remain read-only.
- Render untrusted model output and source text as text, never as executable HTML.
- UI improvements must preserve existing API contracts and local data; migrations and restore-to-live operations require separate reviewed gates.

## Functional coverage

- [ ] Health and runtime/resource status.
- [ ] Chat/research request lifecycle and source/provenance visibility.
- [ ] Learning task creation, progress, assessment, curriculum enrollment, review, and recommendations.
- [ ] Durable task list, pause/resume/cancel, refresh persistence, and recoverable errors.
- [ ] Knowledge and experience memory: capture consent, consent revocation, provenance, review state, search, and persistence.
- [ ] Storage health, backup creation/listing, retention preview, and restore staging; no silent replacement of live databases.
- [ ] Cloud-resource discovery/planning is visibly read-only; no remote provisioning or execution.
- [ ] Governed self-improvement proposals require explicit confirmation; no automatic merge or deployment.

## Required verification

- [ ] Browser E2E covers each primary workflow and important error/empty/loading states.
- [ ] Keyboard-only navigation and visible focus work for all controls.
- [ ] RTL Persian, English LTR, and mixed-direction content render correctly.
- [ ] Responsive checks at 360px, 768px, and desktop widths; no horizontal page overflow.
- [ ] Automated accessibility checks plus manual semantic/keyboard review.
- [ ] Security regression tests confirm local-only network boundary and safe text rendering.
- [ ] Existing backend tests and dependency audit pass on the exact PR head.
- [ ] Documentation screenshots or descriptions match the shipped UI; no completion claim before target-host acceptance.

## Status semantics

A checklist item may be marked complete only with a test name, CI run, or reproducible target-host evidence. If a gate requires user-machine access or an independent reviewer, it must remain explicitly blocked until that evidence is supplied.

## Evidence recorded for the initial UI foundation

The following repository-level checks passed on code/test commit `62b6767828775d69a8612c2b583232750528ce64`, which contains the current navigation and RTL/LTR assertions. A documentation-only commit followed; CI must be green again on the final PR head before merge:

- [x] Local stylesheet is served from the app; no remote font/CDN dependency is introduced.
- [x] Navigation links resolve to all dashboard workflow sections; skip-to-content is the first keyboard stop.
- [x] When `/api/health` reports `untracked_storage_schema`, an accessible degraded-storage alert explains that data is not reset and provides a read-only diagnostic command; the browser E2E test covers this state. CI evidence is pending for the current head.
- [x] Focus-visible and reduced-motion CSS rules are present.
- [x] Text entry follows typed-script direction for English/Persian; code and patch fields remain left-to-right.
- [x] Chromium viewport checks cover 360px, 768px, and 1280px without page-level horizontal overflow.
- [x] Browser E2E workflow: [run 39030101108](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39030101108).
- [x] Full test and Windows storage regression workflows: [run 39030101107](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39030101107).
- [x] Dependency audit: [run 39030101121](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39030101121).

These checks establish the UI foundation only. They do **not** complete the broader functional E2E matrix, target-host usability acceptance, manual keyboard/screen-reader review, contrast audit, or release acceptance. Those gates remain open.

