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