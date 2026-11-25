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


## 2026-11-22 — Autonomous Engineering console

- [x] UI control/event/API/state inventory و transition map ثبت شد.
- [x] PR #837 domain-write fencing در main ادغام شد؛ Linux 2511/14/69 و Windows 2509/16/69، Windows storage، Browser E2E، Docker sandbox، CodeQL و dependency audit سبز.
- [x] PR #836 head قبلی Linux 2491/14/77 و Windows 2489/16/77، Browser E2E، Windows storage، CodeQL، Analyze و dependency audit را گذراند.
- [x] ماژول‌های transport/status/controls/runs/progress و regressionهای stale-response، polling، multi-tab، model-pull، structured error، RTL/mobile/keyboard و WCAG موجودند.
- [ ] exact-head CI برای timeout fix و browser trace artifact.
- [ ] fault matrix کامل و correlation ID تا workflow run/job/task.
- [ ] manual keyboard/screen-reader و rendered-state contrast review.
- [ ] Windows target-host UI/Runner/Ollama/Codex، crash/restart، restore/rollback و owner approval.

Browser E2E از APIهای deterministic/stubbed استفاده می‌کند و جایگزین پذیرش واقعی میزبان نیست.


## 2026-11-23 — بازاعتبارسنجی Autonomous Engineering و correlation end-to-end

- مبنای `main`: `31349a99b197fe702e11e8a5dd53d52ba924575c`؛ PR فعال برای این اصلاحات: [#846](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/846)، Draft.
- **Gate A:** inventory کنترل→API→مالک state→transition در `docs/UI_AUTONOMOUS_ENGINEERING_INVENTORY_2026-11-22.fa.md` موجود است؛ این addendum مسیر correlation مرورگر را هم ثبت می‌کند. بازتولید روی میزبان محلی هنوز pending است.
- **Gate B:** lease epoch/fencing و ownership قبلی در main حفظ شده‌اند. PR #846 وابستگی نصب Broker به Runner را حذف می‌کند؛ Broker با LocalService حتی در نبود Runner نصب می‌شود و Runner فقط قابلیت اجرای اختیاری است. exact-head CI و آزمون Windows واقعی pending است.
- **Gate C:** API structured errors و JSONL recovery در main هستند. PR #846 شناسهٔ correlation را از مرورگر در header درخواست می‌فرستد و در timeout/network failure هم به UI بازمی‌گرداند؛ تست browser round-trip اضافه شده است.
- **Gate D/E:** ماژول‌های مستقل UI، stale-response guard، polling و کنترل‌های fail-closed در main موجودند؛ regression جدید dispatch/timeout باید در Browser E2E exact-head بگذرد.
- **Gate F:** فقط نتایج workflow روی SHA نهایی PR قابل قبول‌اند؛ هیچ نتیجه‌ای از SHA قدیمی به head جدید تعمیم داده نمی‌شود.
- **Gate G/H/J:** اجرای UI با حساب غیرادمین، Runner واقعی، Ollama/Codex، crash/restart، restore/rollback و پذیرش مالک هنوز pending هستند؛ CI hosted جایگزین این شواهد نیست. در اجرای عادی، خطای Broker/Ollama دیگر نباید مانع بازشدن UI شود؛ حالت محدود باید وضعیت ناقص را نشان دهد و --check-only باید کد خروج غیرصفر بدهد.
- هیچ task، owner metadata، checkpoint، database، Runner registration یا دادهٔ محلی حذف/بازنویسی نشده است. PR #846 Draft می‌ماند و merge انجام نمی‌شود تا CI و پذیرش میزبان تکمیل شوند.


## 2026-11-23 — CI exact-head تکمیل شد

- کد PR #846 در SHA `d993038033d5d15cca4a4a6c63c979c081c89aad` تمام CI را گذراند.
- Gate E: Browser E2E موفق؛ صفحهٔ Autonomous Engineering و شش فایل UI بارگذاری شدند؛ diagnostics مرورگر صفر خطا گزارش کرد. run #45131387286، artifact #14797757907.
- Gate F: کامل؛ Ubuntu 2,555 passed / 14 skipped / 78 deselected، Windows 2,550 passed / 19 skipped / 78 deselected، Windows storage regressions موفق، Python bootstrap در سه سیستم‌عامل موفق، CodeQL و Dependency Audit موفق.
- Gate B/C: پیاده‌سازی Broker مستقل از Runner، startup محدود UI و correlation از مرورگر تا task در کد و regressionها پوشش داده شده‌اند؛ این مورد به‌معنای پذیرش Runner واقعی یا dispatch واقعی روی میزبان کاربر نیست.
- Gate G/H/J: هنوز pending؛ آزمون روی Windows واقعی با حساب غیرادمین، Runner واقعی و protected .runner، نبود Runner، Ollama/Codex واقعی، dispatch، pause/resume/cancel، crash/restart و checkpoint preservation باید انجام شود.
- PR #846 Draft می‌ماند تا Gateهای میزبان واقعی تأیید شوند؛ PR #847 mirror بسته شده و branch آن حفظ شده است.

---


## 2026-11-24 — current Autonomous Engineering first-module acceptance snapshot

- PR [#864](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/864), exact head `8303afa5d44f104c6e9a5d569868f3db248023b0), completed all repository CI successfully:
  - Linux suite: 2,576 passed, 14 skipped, 79 deselected.
  - Windows suite: 2,571 passed, 19 skipped, 79 deselected.
  - Windows storage regressions, Browser E2E, CodeQL, dependency audit, and managed Python bootstrap on Windows/Ubuntu/macOS passed.
  - Browser E2E run: [45274156612](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45274156612); tests run: [45274156477](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45274156477).
- PR #864 adds serialization for post-mutation status refreshes. It remains Draft and is not merged.
- Additional safety fixes are under exact-head validation:
  - [#865](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/865): machine-wide mutex held by the elevated State Broker child, addressing parent-timeout/orphan-child overlap; stacked on PR #863.
  - [#866](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/866): live repository Pause-variable read after workflow concurrency admission, rather than relying on a stale workflow-context snapshot.
  - [#867](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/867): cancellation prefers an executing workflow over a newer queued workflow.
- PR #863 remains Draft; the elevated-child timeout finding is separately recorded in its review. PRs #865–#867 remain Draft; their CI must be evaluated against their latest exact heads, not earlier commits.
- Repository CI is not target-host acceptance. Windows non-admin UI startup, UAC, actual Runner identity/ACL, real Ollama/Codex execution, pause/resume/cancel, parent-timeout/elevated-child behavior, crash/restart, checkpoint preservation, keyboard/screen-reader and visual accessibility review, and owner acceptance remain pending.
- No PR was merged and no local service, ACL, Runner registration, task, checkpoint, database, or durable state was changed during this validation work.



### Live Pause and execution-mode admission hardening — 2026-11-24

- The pre-task workflow gate now reads live Pause and execution-mode variables after concurrency admission; the cycle controller repeats both checks after acquiring the repository execution lock and again before dispatching the next continuous cycle after an owner merge.
- Automatic recovery no longer trusts workflow-context PAUSED or EXECUTION_MODE snapshots. It reads live mode/Pause before retry classification and again after backoff immediately before dispatch.
- Missing Pause variable (404) means false; missing mode variable means continuous. API errors and malformed values fail closed. Scheduled mode suppresses queued automatic and recovery runs, while explicit manual tasks and one-shot resume remain allowed.
- ADR-024 records the admission and one-shot Resume contract. PR #866 contains the implementation. Its exact-head CI is pending; no real task/recovery workflow was dispatched for this validation.

## 2026-11-24 — وضعیت پذیرش ماژول Autonomous Engineering

- [x] Refresh وضعیت پس از چند mutation هم‌زمان سریالی شده است؛ regression مرورگر در PR #864 روی exact head موفق شد.
- [x] Cancel اجرای واقعی Codex را از jobهای صرفاً preflight/concurrency تفکیک می‌کند؛ regressionهای queued/in-progress و bound بررسی job روی exact head PR #867 موفق شدند.
- [x] شاخهٔ State Broker، regressionهای UI و broker را حفظ می‌کند؛ شاخهٔ live Pause/Mode، resume یک‌باره در حالت scheduled را نگه می‌دارد.
- [ ] exact-head CI برای head نهایی PRهای #863/#869 و #866/#870 پس از آخرین اصلاحات.
- [ ] پذیرش روی Windows واقعی: non-admin/UAC، Broker، Runner موجود با .runner محافظت‌شده، حالت بدون Runner، ACL و restart.
- [ ] اجرای واقعی Ollama/Codex، dispatch، Pause/Resume/Cancel، timeout، crash/restart و حفظ checkpoint.
- [ ] بازبینی دستی keyboard/screen-reader و تأیید نهایی مالک.
- [ ] secret خواندن Variables برای workflowهای #870 باید توسط مالک ایجاد و روی Runner هدف آزمایش شود؛ تا آن زمان این مسیر عملیاتی پذیرفته نیست.- [x] Resume و manual dispatch از reservation دو مرحله‌ای مشترک و پایدار در State Broker استفاده می‌کنند؛ در صورت وجود run فعال فقط Pause برداشته می‌شود و workflow تکراری dispatch نمی‌شود. Pause برای dispatch جدید فقط پس از پذیرش GitHub برداشته می‌شود؛ نتیجهٔ نامشخص reservation را محافظه‌کارانه نگه می‌دارد.

- [x] Cancel بر اساس run_id snapshot UI، با lookup مستقیم همان run، بررسی workflow/job Codex و همان lock مشترک انجام می‌شود؛ لغو run دیگری از فهرست مجاز نیست.
- [ ] اجرای این regressionها روی exact headهای نهایی #863/#869 و #866/#870 و تأیید CI.


## 2026-11-24 — پذیرش یافته‌های backend و Codex/UI

- [x] gh workflow dispatch از --raw-field برای manual_task و correlation_id و resume_once استفاده می‌کند؛ مستند رسمی GitHub CLI تفاوت raw-field با field و @ syntax را مشخص کرده است.
- [x] diagnostics اجرا فقط برای runهای ناموفق صفحهٔ جاری خوانده می‌شود و تأیید run ID با یک API lookup مستقیم انجام می‌شود.
- [x] Codex retry، typed report failure، نبود پیام نهایی بدون retry کور، timeout کوتاه report-only و marker در آخرین خط غیرخالی پوشش داده شده‌اند.
- [x] UI sticky message، keyed log panels، poll failure safety controls، retryable، cleanup partial failure، invalid date، interval fallback و response-body timeout اصلاح شده‌اند.
- [ ] exact-head CI روی آخرین SHA و هر دو سیستم‌عامل.
- [ ] build و integration واقعی Windows State Broker، سپس پذیرش Windows target-host و browser E2E.
- [ ] اجرای واقعی workflow/Codex و تأیید نهایی مالک.

## 2026-11-25 — integrated startup readiness and Broker portability

- Startup performs read-only Broker and Ollama readiness checks; it does not request UAC, install/repair a service, start Ollama, or pull a model.
- The dashboard exposes explicit Broker setup and refreshes readiness after the operation. Runner provisioning remains a separate privileged operation and is not required to install/use the local Broker.
- The Windows Broker package is built and SHA-256 verified in CI; the target host no longer compiles C# or requires a local compiler.
- PowerShell host selection is capability-based; the elevated child owns the machine mutex, abandoned setup is fail-closed, and ACL changes are transactional with rollback.
- Ollama/model readiness is checked per capability. Missing models are not silently downloaded or substituted.
- GitHub workflow dispatch uses --raw-field for task and correlation input; literal @path text remains data.
- Final acceptance still requires exact-head CI on the combined branch and live Windows target-host verification.

## 2026-11-25 — harden read-only Variables permission verification

- GitHub may return HTTP 404 both for an absent repository variable and for a token that cannot access the variable endpoint. Repository metadata access alone does not prove the fine-grained token has Variables: read.
- The live workflow gate, cycle controller, and recovery coordinator now verify the repository Variables collection endpoint before treating a named-variable 404 as an unset value. The collection response must have a valid non-negative total_count and a variables array.
- This verification is read-only and only runs on the missing-variable path; the dedicated fine-grained credential is never used for Actions, PR, or repository mutations.
- Invalid, inaccessible, malformed, or revoked read permission fails closed; no task or recovery dispatch is accepted.


## 2026-11-25 — یکپارچه‌سازی ماژول مهندسی و CI دقیق

- شاخهٔ integration: `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`؛ head کد `583f482af18b2921225ce91ae1d3ebdc5d765aec`؛ مبنای `main` برابر `8b7c20c50ef4fd9346e924d212a35f36ff646515` است. مقایسهٔ زندهٔ GitHub وضعیت `ahead` با `behind=0` و 69 فایل تغییرکرده را نشان می‌دهد.
- PR اصلی: [#872](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/872)، همچنان Draft و باز؛ هیچ merge انجام نشده است.
- چهار workflow روی exact head موفق‌اند:
  - Tests شامل Linux/Windows، Windows storage/Broker integration و managed Python bootstrap: [run 45595886385](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886385).
  - Browser E2E: [run 45595886850](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886850).
  - CodeQL: [run 45595886382](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886382).
  - Dependency audit: [run 45595886307](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886307).
- در Windows storage job، build/attestation بستهٔ Broker، LocalService و Named Pipe، ACL، PowerShell Desktop/Core، identity portability، checkpoint/recovery، process lock و SQLite restore موفق شدند.
- اصلاحات اصلی: `--raw-field` برای workflow inputs؛ durable cross-process dispatch admission؛ Resume یک‌باره با live Pause/Mode gate؛ exact-run Cancel؛ Broker setup صریح و startup فقط‌خواندنی؛ Broker package قابل‌راستی‌آزمایی؛ Codex report/retry diagnostics؛ UI state/polling hardening.
- بررسی فقط GitHub/کد/CI بوده است. هیچ task، checkpoint، Runner، service، ACL، secret، repository variable یا مدل Ollama روی میزبان مالک تغییر نکرده و workflow اجرای واقعی Codex dispatch نشده است.
- گیت‌های target-host همچنان بازند: اجرای UI روی Windows واقعی با حساب non-admin، UAC/Broker/Runner واقعی، Ollama/Codex واقعی، task غیرمخرب تا PR، Pause/Resume/Cancel، crash/restart و checkpoint preservation؛ همچنین scope توکن Variables: read و پذیرش دستی accessibility/مالک.
- نتیجهٔ تاریخی: Gateهای repository CI و Browser E2E روی SHA قبلی موفق بودند؛ این نتایج برای code head جدید evidence محسوب نمی‌شوند. ماژول هنوز operationally accepted یا ready-to-merge نیست و PR تا تکمیل گیت‌های فعلی و میزبان Draft می‌ماند.


## 2026-11-25 — current integrated acceptance gates

- [x] Startup checks are read-only: no implicit Broker UAC/setup, no automatic Ollama server start, and no automatic model pull.
- [x] Broker setup is a separate explicit dashboard/API action; missing Broker limits the dependent capability rather than silently mutating Windows services or ACLs.
- [x] PowerShell host selection is capability-based; Security module import is isolated to the active host's `$PSHOME\Modules` path and a Windows CI integration test verifies behavior with inherited PowerShell 7 module paths.
- [x] Ollama Context is calculated from installed base-model metadata and currently available hardware memory.
- [x] Each autonomous run creates a unique temporary context tag containing context, run ID, attempt, and nonce; the exact tag is recorded before model creation.
- [x] Main and diagnostic workflows have an `always()` cleanup step that unloads and deletes only the exact run-owned temporary tag and verifies its absence.
- [x] Base model is required to remain installed; unrelated model tags are never deleted by temporary cleanup.
- [x] Legacy numeric context aliases are audit-only by default; deletion requires explicit `--delete-confirmed-legacy`.
- [x] UI/API model selectors reject internal context variants, including run-scoped temporary tags, as base-model choices.
- [x] Legacy settings migration seeds a missing chat profile from the base tag and repairs an existing chat profile only when it contains a reserved ephemeral Codex context tag; valid user model choices are preserved, and the reserved suffix is normalized regardless of whether it came from legacy settings or an environment-seeded default.
- [x] Regression tests cover migration of an existing temporary chat tag, preservation of ordinary existing chat models, normalization when an environment-seeded default contains an ephemeral tag, and behavior when the legacy generic model key is absent (`tests/test_db_schema.py` and `tests/test_core.py`).
- [ ] Exact-head Linux/Windows tests, Windows storage/Broker integration, Browser E2E, CodeQL, and dependency audit must all complete successfully on the final SHA.
- [ ] Windows target-host acceptance: reproduce the prior PowerShell 5.1 duplicate-TypeData failure and verify the corrected import, Broker health/setup, Runner identity/ACL, and restart behavior.
- [ ] Exact-head Windows CI must pass PowerShell 5.1 regressions for clean import, repeated -Force import, and reuse of a preloaded Security module with inherited PowerShell 7 module paths; owner-host verification must confirm the previously failing module load and Broker health.
- [ ] Live Ollama/Codex acceptance: verify available-RAM context selection, real tool call, successful-task cleanup, failed-task cleanup, and preservation of the base model.
- [ ] End-to-end UI acceptance: dispatch a harmless task, observe progress/logs/PR/exact-SHA checks, then test Pause/Resume/Cancel and crash/restart checkpoint preservation.
- [ ] Keyboard, screen-reader, contrast, and owner visual acceptance.
- No merge is permitted while any required gate above remains open. CI success is not a substitute for target-host or owner acceptance.

