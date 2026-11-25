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
