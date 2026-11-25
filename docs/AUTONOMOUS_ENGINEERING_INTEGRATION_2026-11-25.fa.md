## 2026-11-25 — latest root-cause correction and validation snapshot

### Code and branch state
- Main base: `8b7c20c50ef4fd9346e924d212a35f36ff646515`.
- Integration branch: `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`.
- Code head for the lifecycle correction: `d54f62c4460805f58eb789c655491dfc950ee789`; subsequent commits in this report update documentation and PR metadata only.
- PR #872 is the sole active integration PR, based directly on `main`, open and Draft. PR #875 was merged into the #872 branch (not into main). PRs #863 and #869 (including the #868 follow-up already merged into #863), #866, #870, #871 and documentation-only PR #876 are closed as superseded; their branches are retained.
- The integration branch includes 81 changed files and is ahead of main with behind=0 at the last live comparison before the final documentation-only updates.

### Newly confirmed root causes and corrections

1. **Implicit model pull:** main's previous startup path treated an internal Codex context tag such as `qwen2.5:7b-codex-ctx2048` as a registry model and attempted `ollama pull`. This caused `pull model manifest: file does not exist`. The integration branch performs read-only model inventory checks, identifies internal variants, does not pull or substitute models, and leaves model selection explicit.
2. **Persistent context variant:** the previous context preflight calculated a safe context from available RAM but created a shared persistent `<base>-codex-ctx` tag; the post-task step only verified allocation and did not remove it. The corrected workflow creates a unique run-scoped tag `<base>-codex-ctx<context>-paa-<run-id>-<attempt>-<nonce>`, records it before creation, and unloads/deletes only that tag in an `always()` cleanup step after task success or failure.
3. **Unsafe legacy sweep:** legacy numeric aliases have no run-ownership proof. Their cleanup is audit-only by default; destructive deletion requires explicit `--delete-confirmed-legacy`. The current task cleanup never sweeps other model tags.
4. **PowerShell Security import:** the Windows identity module isolates `PSModulePath` to the active PowerShell host's `$PSHOME\Modules` while importing the built-in Security module, avoiding PowerShell 5.1/7 cross-edition TypeData collisions. The target-host reproduction is still required.
5. **Model selector boundaries:** API/status, startup preflight, provisioning, and Windows readiness checks reject run-scoped temporary variants as base-model selections.

### Regression coverage added or updated
- Temporary tag format binds base model, context, run ID, attempt, and nonce.
- Cleanup is idempotent and preserves the base model and unrelated model tags.
- Wrong run, attempt, context, legacy name, or unowned tag is rejected before deletion.
- Loaded models are unloaded and checked through `/api/ps`; deletion is verified through `/api/tags`.
- Failed unload, failed deletion, or disappearance of the base tag fails closed.
- Legacy alias cleanup defaults to audit-only; explicit deletion requires the confirmation flag.
- Both autonomous and Windows diagnostic workflows run the exact temporary-model cleanup path.

### Current CI evidence boundary
For code head `d54f62c4460805f58eb789c655491dfc950ee789`:
- Tests: [45623237155](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45623237155) — jobهای اصلی Linux/Windows و Windows storage/Broker در صف؛ bootstrap macOS موفق.
- Dependency audit: [45623237087](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45623237087) — موفق روی code head فعلی.
- CodeQL: [45623237128](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45623237128) — پس از rerun در صف.
- Browser E2E: [45623237170](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/45623237170) — موفق.
- Browser E2E was not triggered on this code head. A previous Browser E2E run on an earlier SHA is not exact-head evidence.
- The successful CI recorded earlier for `583f482af18b2921225ce91ae1d3ebdc5d765aec` is historical and does not validate the new lifecycle changes.

### Remaining acceptance gates
- Exact-head Tests, Windows full suite, Windows storage/Broker integration, Browser E2E, CodeQL and dependency audit must complete successfully for the final code head.
- Reproduce and resolve the user's Windows PowerShell 5.1 duplicate-TypeData error on the actual target host.
- Verify Broker setup/health, non-admin Runner identity and ACLs on the target host.
- Run a harmless end-to-end task from the UI through dispatch, Codex, progress, PR and exact-SHA checks.
- Verify successful and failed task cleanup against the real local Ollama API; prove the base model remains installed and the run-scoped variant disappears.
- Exercise Pause/Resume/Cancel, crash/restart, checkpoint/worktree preservation and manual accessibility/owner acceptance.
- No merge to main is allowed while these gates remain open.

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
