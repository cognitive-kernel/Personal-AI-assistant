# UI acceptance criteria

## Automated repository-level gates

- [x] Local stylesheet and local-only dependencies (no CDN, remote fonts, or telemetry).
- [x] Navigation anchors resolve to each primary dashboard workflow.
- [x] Malformed percent-encoded dashboard routes fall back to the chat page without a JavaScript exception; covered by `tests/test_browser_e2e.py::test_dashboard_malformed_percent_encoded_route_falls_back_without_script_error`. Exact-head CI passed on [PR #550](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/550); merge-commit CI passed on `ec9f0e0e0c8d2b91f7c8ad176cdc93d72e5a86c6`.
- [x] Persistent page navigation marks the active route with `aria-current=page`; browser coverage checks all 16 primary destinations and responsive navigation. See [Day 1 UI audit](UI_AUDIT_2026-10-30.md).
- [x] Skip-to-content link and visible focus styling.
- [x] RTL page direction, text-direction handling for Persian/English, and LTR code/patch fields.
- [x] Reduced-motion support and accessible names for controls, including dynamically generated learning review fields.
- [x] Chromium checks at 360px, 768px, and 1280px for horizontal overflow.
- [x] Learning-path progress is a native accessible progress bar derived from completed/total topics; tests and Windows storage regressions passed on [code head `83bee038`](https://github.com/cognitive-kernel/Personal-AI-assistant/commit/83bee038ab9af43f89bcadd4809bd3e210a84b01), with [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39367284798) and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39367284734) green.
- [x] Task dashboard distinguishes an empty queue from zero search matches and offers an accessible clear-filters action; exact-head [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001558), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001660), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39368001638) passed on code head `134168220519480a480a60ebafff2bb60dba7f85`.
- [ ] Full manual keyboard and screen-reader review.
- [x] Automated WCAG contrast regression for core light/dark design tokens; manual rendered-state contrast review and target-host usability acceptance remain open. The token-level check lives in `tests/test_ui_contrast.py`.
- [ ] Broader functional parity and operational E2E matrix.
- [x] Sandbox consent and bounded execution browser flow: Chromium verifies fail-closed behavior on status lookup failure, explicit consent before execution, timeout/output bounds in the submitted payload, task completion rendering, and execution disablement after consent revocation. Exact-head [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483069), [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483105) (including Windows storage regressions), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41018483133) passed for PR [#574](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/574), merged as `968b10bc7d731f7a241fbc9174d2837d994ed514`. This mocked browser test does not replace target-host Docker/runtime acceptance.
- [x] Task priority and scheduled-time controls for queued/paused tasks; exact-head CI passed in [PR #355](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/355).
- [x] Task queue live summary and client-side search over already-loaded task pages; all exact-head CI checks passed in [PR #356](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/356).
- [x] Bulk pause/resume/cancel controls for selected tasks; exact-head CI passed in [PR #357](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/357).
- [x] Semantic visual state styling and Persian status labels for task cards; browser assertions cover state classes, raw status identifiers, localized labels, and accessible names. Exact-head tests, Windows storage regressions, browser E2E, and dependency audit passed in [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490380), [browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490415), and [dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39364490478). Manual accessibility and target-host checks remain open.

- [x] Cloud Planner browser workflow: Chromium verifies validated requirements, dry-run rendering, and that execution remains unauthorized; a simulated HTTP 503 is rendered and controls recover. PRs [#576](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/576) and related browser coverage passed exact-head CI. Provider APIs are stubbed; no cloud resource is provisioned.
- [x] Kaggle quota and OCI limits browser workflows: explicit consent, manual read-only refresh, revocation, failure recovery, safe output, and no remote execution authorization. PR [#591](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/591) passed exact-head tests/Windows storage, browser E2E, and dependency audit. The test intercepts provider APIs and does not invoke live CLIs or use credentials.
- [x] Quota snapshot scope integrity: PR [#592](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/592) invalidates old Kaggle/OCI snapshots transactionally when provider identity/scope changes and rejects in-flight results from stale consent generations; ADR-010 and API regression tests document and verify the behavior. Exact-head tests/Windows storage, browser E2E, and dependency audit passed.
- [x] Programming assistance browser workflow and context limit: PR [#593](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/593) aligns textarea maxLength with the persisted setting and verifies task submission, polling, result display, API-failure recovery, and that generated code/tests are not represented as executed. Exact-head tests/Windows storage, browser E2E, and dependency audit passed. Post-merge CI on `main` commit `c5e8a7c338c35b0c9267bb563344b9973ac56153` also passed: tests 1537 passed/5 skipped, Windows storage regressions, browser E2E, and dependency audit.

## Evidence policy

Mark repository-level items complete only after the exact PR head CI passes. Browser automation does not replace manual accessibility review or target-host acceptance. Preserve existing API contracts and local data; do not enable remote provisioning or automatic deployment.

- [x] User-selectable dark theme with OS preference fallback and locally persisted explicit choice; [PR #365](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/365) merged at `9702984f236e0c3fdefad16365e1127027e17b87`. Exact-head tests/Windows regressions, browser E2E, and dependency audit passed. Manual contrast and keyboard/screen-reader review remain separate gates.

- [x] AI-learning course/topic/lesson review and reload persistence: \`test_ai_learning_topic_approval_lesson_review_publish_and_reload_persist\` verifies topic approval, lesson review, publishing, and reconstruction of the published version after page reload using API state. This is mocked Chromium coverage, not live provider validation.
- [x] Sandbox cancellation browser recovery: \`test_browser_sandbox_cancel_recovers_from_api_failure_and_settles_task\` verifies a failed cancellation request remains retryable, a second request succeeds, task polling reaches cancelled, and controls settle. The API is intercepted; real-Docker cancellation remains distinct.
- Current Chromium E2E inventory on main `a174c9e5b4b4841fc7b68fffec107992214f5610`: **54 independent test functions** in `tests/test_browser_e2e.py`. PR [#784](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/784) added seven asynchronous-state and consent fail-closed regressions; PR [#786](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/786) added a stale lesson-list/course-switch regression. Exact-head tests, Windows storage regressions, Browser E2E, Dependency Audit, and CodeQL passed for both. Manual keyboard/screen-reader review, rendered-state contrast review, and target-host usability remain open.

- [x] Dedicated local autonomous-engineering console at `/autonomous-engineering`, with read-only app-account diagnostics, explicit workflow dispatch/cancel controls, structured Codex plan progress, immutable main-scorecard history, bounded activity log, and separate Runner-account preflight. Exact-head PR #680 head `c7ec867df63baac9dec74b736b65a64284a525e4`: 1,840 tests passed/48 deselected, deterministic retrieval benchmark passed, claim-to-test coverage 10/10, Windows storage regressions passed, Chromium dashboard smoke passed, and dependency audit passed. Actual Windows Runner registration, non-admin service account, ACL execution, 64k Ollama context/performance soak, and independent security review remain external gates.
- [x] Shared primary navigation on the AI-learning and autonomous-engineering pages; exact-head unit and Chromium dashboard smoke checks passed on PR #680 head `c7ec867df63baac9dec74b736b65a64284a525e4`.

## Autonomous Engineering UI acceptance gates — 2026-11-22

این بخش مخصوص کنسول Autonomous Engineering است و جایگزین پذیرش عمومی سایر صفحات نیست.

| Gate | معیار | وضعیت |
|---|---|---|
| A — Inventory/reproduction | نگاشت هر کنترل UI به API، مالک state، transition و regression | inventory ثبت شده در `docs/UI_AUTONOMOUS_ENGINEERING_INVENTORY_2026-11-22.fa.md`؛ reproduction میزبان هنوز pending |
| B — Ownership/fencing/recovery | ترتیب repository → durable-state → checkpoint، owner/epoch fencing، fail-closed cleanup و recovery | lease epoch و fencing در main؛ PR #834 قفل repository را داخل broker اعمال می‌کند، writerهای progress پیش از قفل را با file-share guard متوقف می‌کند و انتشار PR را به فرزند cycle controller قفل‌دار محدود می‌کند؛ ممیزی تمام mutation call-siteها و crash recovery pending |
| C — Error/diagnostics | خطای ساختاریافته و گزارش Codex معتبر/ناقص | PR #817 و PR #827 در main ادغام شده‌اند؛ producer واقعی و UI header integration در PR #834 در انتظار CI |
| D — UI state machine | status/run/progress single-flight، feedback پایدار، module fault isolation، کنترل‌های pause/resume/cancel/finish | کد در PR #834؛ پذیرش exact-head pending |
| E — Browser E2E | مسیرهای موفق، خطا، stale response، cancel، cleanup، keyboard و responsive | تست‌ها موجود/افزوده شده‌اند؛ اجرای exact-head pending |
| F — Exact-head CI | Linux/Windows tests، browser E2E، dependency audit، CodeQL | pending؛ هیچ نتیجهٔ SHA قدیمی به head جدید تعمیم داده نمی‌شود |
| G — Windows target host | اجرای واقعی UI با LocalService broker، Runner، Ollama و Codex | pending؛ CI شبیه‌سازی جایگزین میزبان هدف نیست |
| H — Crash/restart | قطع process/service در حین task و بازیابی همان checkpoint/worktree | pending؛ هیچ checkpoint آزمایشی حذف نشده است |
| I — Docs/rollback | inventory، ADR، runbook، restore/rollback و ثبت شناسهٔ اجراها | inventory و ADR در PRها؛ runbook و شواهد restore/rollback pending |
| J — Owner acceptance | مالک UI را روی target host بررسی و تأیید کند | pending؛ تا آن زمان PRها Draft و merge ممنوع |

**مرز ایمنی cleanup:** Windows broker خودش repository lock را می‌گیرد و فقط stateهای تحت مالکیت خود را پاک می‌کند. فایل‌های runtime داخل worktree در Windows حفظ می‌شوند تا بعد از آزادشدن broker lock حذف نشوند. روی POSIX cleanup زیر repository، durable-state و checkpoint lock انجام می‌شود.

**سیاست ادغام:** CI سبز به‌تنهایی به معنی Gate G/H/J نیست. طبق prompt اختصاصی UI، تا تکمیل Gateهای لازم و تأیید مالک، PR #834 و PR کنترل‌های ماژول‌ها نباید ادغام شوند.

## Autonomous Engineering — polling lifecycle follow-up (2026-11-22)

- [ ] Polling controller uses bounded exponential backoff, bounded jitter, visibility-aware scheduling, single-flight refresh, per-request timeout through the shared request helper, and explicit teardown on `pagehide`/app stop. It covers Status, Runs, Progress and model-pull status; model-pull GET errors retry up to five consecutive failures without cancelling the server-side job. Chromium regressions are `test_autonomous_engineering_polling_uses_visibility_backoff_jitter_and_cleanup` and `test_autonomous_engineering_model_pull_requires_consent_and_reports_progress`. Exact-head CI is pending.
