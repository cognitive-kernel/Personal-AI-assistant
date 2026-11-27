## 2026-11-25 — latest root-cause correction and validation snapshot

### Code and branch state
- Main base: `8b7c20c50ef4fd9346e924d212a35f36ff646515`.
- Integration branch: `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`.
- Latest code head: `d1514c636365ee807775aed101a07d9b1e983bd5`; latest documentation/PR head: `a951838b0b5f0bc25d21e145157faaa7ba778444`.
- PR #872 is the sole active integration PR, based directly on `main`, open and Draft. PR #875 was merged into the #872 branch (not into main); superseded PRs are closed and their branches retained.
- Last live comparison: ahead=469, behind=0, 82 changed files. Recheck before merge.

### Newly confirmed root causes and corrections

1. **Implicit model pull:** an internal Codex context tag such as `qwen2.5:7b-codex-ctx2048` was treated as a registry model and triggered an invalid pull. Startup now performs read-only inventory checks, recognizes internal variants, and never pulls or substitutes a model.
2. **Persistent context variant:** previous preflight created a shared persistent `<base>-codex-ctx` tag and did not delete it after a task. Each workflow now creates a unique run-scoped tag `<base>-codex-ctx<context>-paa-<run-id>-<attempt>-<nonce>`, records it before creation, then unloads/deletes only that tag in an `always()` cleanup step after success or failure.
3. **Unsafe legacy sweep:** legacy numeric aliases have no run-ownership proof. Cleanup is audit-only by default; destructive deletion requires explicit `--delete-confirmed-legacy`. Task cleanup never sweeps other tags.
4. **PowerShell duplicate TypeData:** both the identity module and capability probe could re-import `Microsoft.PowerShell.Security`. They now reuse an already-loaded module, import only when absent, reject a module loaded from outside the active host's `$PSHOME\Modules`, verify ACL commands, and restore `PSModulePath`. Windows tests cover clean import, repeated `-Force` import, and reuse of a preloaded module.
5. **Stale chat model profile:** a previous run could leave an ephemeral tag such as `qwen2.5:7b-codex-ctx2048` saved as `chat_ollama_model`. Since the tag is deleted after the task, the next app start could not use chat. Database initialization now normalizes only the reserved internal suffix to its base tag, including when the tag was environment-seeded; ordinary saved model choices and the legacy generic key are preserved. Startup preflight recognizes the same recoverable case and avoids a false unavailable warning when the base model is installed.
6. **Model selector boundaries:** API/status, startup preflight, provisioning, and Windows readiness checks reject internal temporary variants as base-model selections.

### Regression coverage added or updated
- Temporary tag format binds base model, context, run ID, attempt, and nonce.
- Cleanup is idempotent and preserves the base model and unrelated tags.
- Wrong run, attempt, context, legacy name, or unowned tag is rejected before deletion.
- Loaded models are unloaded and checked through `/api/ps`; deletion is verified through `/api/tags`.
- Failed unload/deletion or disappearance of the base tag fails closed.
- Legacy alias cleanup defaults to audit-only; explicit deletion requires the confirmation flag.
- Database tests cover normalization of an existing temporary chat tag, missing legacy generic key, ordinary user-selected chat model preservation, and environment-seeded temporary tags.
- Startup preflight tests cover base-installed recovery and the case where the base model is not installed.
- Windows PowerShell tests cover clean import, repeated import, preloaded Security module reuse, host path validation, and `PSModulePath` restoration.

### Current exact-head CI evidence
For documentation/PR head `a951838b0b5f0bc25d21e145157faaa7ba778444`:
- Tests: [45628406423](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45628406423) — queued.
- Browser E2E: [45628406451](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45628406451) — queued.
- CodeQL: [45628406200](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45628406200) — queued.
- Dependency audit: [45628406224](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45628406224) — queued.
- Previous successful or cancelled runs on older SHAs are not evidence for this head. No exact-head pass is claimed yet.

### Remaining acceptance gates
- All exact-head Tests, Windows full suite, Windows storage/Broker integration, Browser E2E, CodeQL, and dependency audit must complete successfully.
- Reproduce the original PowerShell 5.1 duplicate-TypeData failure on the owner's Windows host and verify the corrected import.
- Verify Broker setup/health, non-admin Runner identity and ACLs on the target host.
- Run a harmless task from the UI through dispatch, Codex, progress, PR, and exact-SHA checks.
- Verify successful and failed task cleanup against real Ollama; prove the base model remains installed and the run-scoped variant disappears.
- Exercise Pause/Resume/Cancel, crash/restart, checkpoint/worktree preservation, accessibility, and owner acceptance.
- No merge to main while any required gate remains open.

---

# گزارش یکپارچه‌سازی و اعتبارسنجی ماژول Autonomous Engineering — 2026-11-25

## مبنا و شاخه
- Repository: `cognitive-kernel/Personal-AI-assistant`
- Base `main`: `8b7c20c50ef4fd9346e924d212a35f36ff646515`
- Integration branch: `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`
- بررسی exact head پیش از ثبت این گزارش: `583f482af18b2921225ce91ae1d3ebdc5d765aec`
- Pull request: [#872](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/872)، Draft و باز.
- مقایسهٔ branch با main: ahead، behind=0، تعداد 69 فایل تغییرکرده.

## قراردادهای حاکم
این کار مطابق `docs/MASTER_ENGINEERING_PROMPT.md` و `docs/MASTER_UI_ENGINEERING_PROMPT.fa.md` انجام شده است. معیار اصلی، عملکرد end-to-end از UI تا Runner/Codex/Ollama/GitHub و حفظ durable state است؛ تست واحد یا سبز شدن CI به‌تنهایی اثبات پذیرش عملیاتی نیست.

## اصلاحات منتقل‌شده/یکپارچه‌شده
1. Dispatch/Resume: ورودی‌های workflow با `--raw-field` ارسال می‌شوند؛ متن literal از جمله `@path` به‌عنوان فایل محلی تفسیر نمی‌شود.
2. Admission: reservation دو مرحله‌ای پایدار در State Broker، قفل cross-process و grace window برای جلوگیری از dispatch تکراری بین processها و پس از restart.
3. Pause/Mode: کنترل زنده پس از concurrency admission و بازبینی در cycle/recovery؛ خطا یا مجوز ناکافی fail-closed است.
4. Resume: یک‌باره و idempotent؛ اگر run فعال موجود باشد فقط Pause برداشته می‌شود و run دوم dispatch نمی‌شود.
5. Cancel: run_id از آخرین snapshot معتبر UI دریافت و همان run/workflow/job دوباره بررسی می‌شود؛ هدف مبهم fail-closed است.
6. Windows Broker: startup فقط‌خواندنی، setup صریح، PowerShell capability discovery، mutex متعلق به child ممتاز، abandoned-setup marker، ACL rollback و package ساخته‌شده/attested در CI.
7. Local-first/readiness: وابستگی‌های Ollama، مدل، GitHub، Runner و Broker جداگانه گزارش می‌شوند؛ نصب/شروع سرویس یا pull مدل در startup انجام نمی‌شود.
8. Codex: طبقه‌بندی failure، diagnostic پالایش‌شده، retry محدود، اعتبارسنجی completion marker و حفظ checkpoint در failure.
9. UI: sticky action feedback، polling/state synchronization، stale-response guards، fault isolation، exact-run cancellation و پیام خطای قابل پیگیری.
10. تست و اسناد: regression tests، Browser E2E، ADR، inventory، runbook، Engineering Log، Project Status و UI Acceptance به‌روز شده‌اند.

## شواهد exact-head CI
Head بررسی‌شده: `583f482af18b2921225ce91ae1d3ebdc5d765aec`.

| Workflow | نتیجه | شواهد |
|---|---|---|
| Tests | موفق | [45595886385](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886385) |
| Browser E2E | موفق | [45595886850](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886850) |
| CodeQL | موفق | [45595886382](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886382) |
| Dependency audit | موفق | [45595886307](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45595886307) |

Windows storage integration در workflow تست، build/attestation Broker، LocalService/Named Pipe، ACL، PowerShell Desktop/Core، هویت Windows، checkpoint/recovery، process lock و SQLite restore را با موفقیت اجرا کرده است.

## مواردی که هنوز اثبات نشده‌اند
- اجرای برنامه روی Windows واقعی مالک و حساب non-admin.
- UAC و نصب/ترمیم Broker و بررسی سرویس LocalService روی میزبان واقعی.
- Runner واقعی در حالت حاضر و غایب و ACLهای واقعی آن.
- Ollama و مدل مهندسی واقعی `gpt-oss:20b`، Codex CLI و tool call واقعی.
- task غیرمخرب از UI تا dispatch، progress، logs، PR و exact-SHA checks.
- Pause/Resume/Cancel زنده و crash/restart با حفظ task/checkpoint/worktree.
- وجود و scope توکن `AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN` با مجوز فقط repository-scoped `Variables: read`.
- keyboard/screen-reader/contrast review و پذیرش مالک.

## ایمنی
هیچ task، checkpoint، workflow زنده، Runner registration، service، ACL، secret، repository variable یا مدل Ollama روی میزبان مالک در این بررسی تغییر نکرد. هیچ task واقعی Codex dispatch نشده است. هیچ PR یا branch حذف نشده و merge خودکار انجام نشده است.

## تصمیم
گیت‌های CI و Browser E2E روی exact head موفق‌اند. بااین‌حال، تا تکمیل target-host acceptance و تأیید مالک، PR #872 باید Draft بماند و ماژول نباید آمادهٔ انتشار/ادغام اعلام شود.
