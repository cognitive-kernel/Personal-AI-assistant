# UI acceptance criteria

## Automated repository-level gates

- [x] Local stylesheet and local-only dependencies (no CDN, remote fonts, or telemetry).
- [x] Navigation anchors resolve to each primary dashboard workflow.
- [x] Persistent navigation marks the current section with `aria-current=location`, updates on scroll/resize/hash navigation, and uses a responsive active indicator.
- [x] Skip-to-content link and visible focus styling.
- [x] RTL page direction, text-direction handling for Persian/English, and LTR code/patch fields.
- [x] Reduced-motion support and accessible names for controls, including dynamically generated learning review fields.
- [x] Chromium checks at 360px, 768px, and 1280px for horizontal overflow.
- [x] Learning-path progress is a native accessible progress bar derived from completed/total topics; tests and Windows storage regressions passed on [code head `83bee038`](https://github.com/cognitive-kernel/Personal-AI-assistant/commit/83bee038ab9af43f89bcadd4809bd3e210a84b01), with [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39367284798) and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39367284734) green.
- [x] Task dashboard distinguishes an empty queue from zero search matches and offers an accessible clear-filters action; exact-head [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001558), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001660), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001638) passed on code head `134168220519480a480a60ebafff2bb60dba7f85`.
- [ ] Full manual keyboard and screen-reader review.
- [ ] Contrast audit and target-host usability acceptance.
- [ ] Broader functional parity and operational E2E matrix.
- [x] Task priority and scheduled-time controls for queued/paused tasks; exact-head CI passed in [PR #355](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/355).
- [x] Task queue live summary and client-side search over already-loaded task pages; all exact-head CI checks passed in [PR #356](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/356).
- [x] Bulk pause/resume/cancel controls for selected tasks; exact-head CI passed in [PR #357](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/357).
- [x] Semantic visual state styling and Persian status labels for task cards; browser assertions cover state classes, raw status identifiers, localized labels, and accessible names. Exact-head tests, Windows storage regressions, browser E2E, and dependency audit passed in [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490380), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490415), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490478). Manual accessibility and target-host checks remain open.

## Evidence policy

Mark repository-level items complete only after the exact PR head CI passes. Browser automation does not replace manual accessibility review or target-host acceptance. Preserve existing API contracts and local data; do not enable remote provisioning or automatic deployment.

- [x] User-selectable dark theme with OS preference fallback and locally persisted explicit choice; [PR #365](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/365) merged at `9702984f236e0c3fdefad16365e1127027e17b87`. Exact-head tests/Windows regressions, browser E2E, and dependency audit passed. Manual contrast and keyboard/screen-reader review remain separate gates.
