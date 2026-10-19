# UI acceptance criteria

## Automated repository-level gates

- [x] Local stylesheet and local-only dependencies (no CDN, remote fonts, or telemetry).
- [x] Navigation anchors resolve to each primary dashboard workflow.
- [x] Skip-to-content link and visible focus styling.
- [x] RTL page direction, text-direction handling for Persian/English, and LTR code/patch fields.
- [x] Reduced-motion support and accessible names for controls, including dynamically generated learning review fields.
- [x] Chromium checks at 360px, 768px, and 1280px for horizontal overflow.
- [ ] Full manual keyboard and screen-reader review.
- [ ] Contrast audit and target-host usability acceptance.
- [ ] Broader functional parity and operational E2E matrix.
- [x] Task priority and scheduled-time controls for queued/paused tasks; exact-head CI passed in [PR #355](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/355).
- [x] Task queue live summary and client-side search over already-loaded task pages; all exact-head CI checks passed in [PR #356](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/356).
- [ ] Bulk pause/resume/cancel controls for selected tasks; exact-head CI pending.

## Evidence policy

Mark repository-level items complete only after the exact PR head CI passes. Browser automation does not replace manual accessibility review or target-host acceptance. Preserve existing API contracts and local data; do not enable remote provisioning or automatic deployment.
