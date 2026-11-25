## 2026-11-24 — PR #872 diagnostic polling cache follow-up

- Added a 60-second bounded cache for failed-run diagnostics to prevent repeated Broker reads during dashboard polling; regression coverage verifies reuse across consecutive polls.
- The cache is an optimization; Broker-backed diagnostics remain authoritative.
- PR #872 exact-head tests, Browser E2E and dependency audit must pass on the final updated SHA. Windows target-host acceptance remains pending and is not replaced by CI.
- The stacked PR base chain (#866 → #870 → #872) remains unresolved; do not merge until the branch history is safely reconciled and every required acceptance gate passes.

---

## 2026-11-24 — PR #872: تکمیل اصلاحات ماژول Autonomous Engineering و بازاعتبارسنجی

- PR: [#872](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/872)، شاخهٔ `fix/autonomous-engineering-backend-ui-hardening-20261124-rebased`؛ همچنان stacked روی #870 و #866 است و نباید مستقل از baseهایش merge شود.
- بازبینی دو پرامپت اجرایی، UI acceptance، UI audit و وضعیت main انجام شد. baseline fencing/lease epoch از PR #799 حفظ شده؛ این تغییرات آن را بازنویسی یا حذف نمی‌کنند.
- ۱۴ مورد گزارش‌شده در backend، retry/Codex و UI در کد شاخه پیاده‌سازی شده‌اند؛ در بازبینی CI، چند regression harness قدیمی و دو شکاف واقعی تکمیلی شناسایی شد.
- اصلاحات تکمیلی پس از CI ناموفق: تست Resume اکنون اجرای موفق پاک‌کردن Pause را قبل از dispatch شبیه‌سازی می‌کند؛ خطای قطعی launch صریحاً retryable است؛ retry گزارش حفظ worktree/checkpoint را صریح می‌گوید؛ marker فقط از آخرین خط غیرخالی ارزیابی می‌شود؛ تست‌ها با API جدید sticky status و credential consumption هماهنگ شده‌اند؛ POSIX broker اکنون blockerهای reservation/grace را مانند Windows تفکیک می‌کند؛ fixture قدیمی وابسته به متغیر حافظه‌ای حذف شده است.
- شکاف رفتاری افزوده‌شده: Resume روی صف idle و از قبل unpaused دیگر workflow تازه dispatch نمی‌کند؛ اگر run فعال باشد، فقط همان اجرا ادامه می‌یابد.
- CI قبلی روی SHA `82c44e04d44f6366c307440175352fe847621781` در tests شکست خورد؛ Browser E2E، dependency audit و Windows storage regressions موفق بودند. علت‌های تستی/قراردادی و اصلاحات آن‌ها در Engineering Log ثبت می‌شوند.
- SHA این snapshot پیش از اصلاح مستندات: `784b80abbe97e3f2bd3089d6e1e8c235abd2df0d`. exact-head tests، Browser E2E و dependency audit در صف اجرا هستند؛ هیچ pass برای این SHA ادعا نمی‌شود.
- Windows target-host، Runner/Ollama/Codex واقعی، dispatch/pause/resume/cancel عملیاتی، crash/restart، restore/rollback و پذیرش UI توسط مالک هنوز pending هستند. تا تکمیل base PRها، exact-head CI و Gateهای میزبان، PR Draft می‌ماند و merge ممنوع است.
- هیچ workflow فعالی لغو یا تغییر داده نشد؛ هیچ Pause/Mode secret یا variable، task، checkpoint، Runner، ACL، database یا دادهٔ کاربر تغییر نکرد.

---

## 2026-11-24 — Resume idempotency and safe rollback

- Resume now shares the dispatch mutex, has its own acceptance grace (separate from manual dispatch), and manual dispatch is suppressed immediately after an accepted Resume.
- If a workflow run is already active, Resume clears Pause but does not enqueue a second workflow. If preflight/launch fails definitively, Pause is restored; if the dispatch outcome is ambiguous, the request is marked non-retryable for the visibility window and the operator must inspect run history.
- Regression tests cover lock contention, existing active runs, duplicate Resume, cross-action duplicate dispatch, Pause rollback, and ambiguous outcomes.

## 2026-11-24 — reconciliation after mainline UI/cancel merges

- Current main: ff90d2e3745cb8b2d8dfb08d7b9baddef903d1f1.
- PR #864 (serialized status refresh) and PR #867 (job-aware Cancel) are merged. Their exact-head tests, Browser E2E, CodeQL and dependency-audit checks passed; the feature branches now carry these regressions so later integration does not revert them.
- Parent branches #863 and #866 were updated with the mainline Cancel handler; #863 also carries the serialized status refresh implementation and browser regression. Child branches #869/#870 must be rebased/merged onto their updated parent heads before merge.
- Exact-head CI for the latest child commits is pending. Windows target-host acceptance, Runner/UAC/ACL, Ollama/Codex and actual dispatch/pause/resume/cancel tests remain NOT RUN.
- No workflow was dispatched and no repository secret was created by the assistant. For #870, the owner must create AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN as a fine-grained, repository-scoped token with Variables: read; never paste its value into chat or issues.

---

## 2026-11-24 — اصلاح نهایی منبع اعتبارنامهٔ خواندن متغیرهای Actions

- اصلاح قبلی که از `gh auth token` حساب Runner استفاده می‌کرد، پس از بررسی واقعی `scripts/provision_autonomous_runner.ps1` کنار گذاشته شد: احراز هویت CLI در نشست نصب‌کننده انجام می‌شود، اما Runner تازه‌نصب‌شده معمولاً با حساب محلی جداگانه و غیرادمین اجرا می‌شود و اسکریپت، credential نصب‌کننده را به آن حساب منتقل نمی‌کند.
- مسیر فعلی در branch #870 در هر سه لایهٔ PowerShell live gate، Python cycle controller و Python recovery coordinator از secret اختصاصی `AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN` استفاده می‌کند. این باید یک fine-grained token محدود به همین repository با مجوز **Variables: read** باشد؛ فقط برای GETهای متغیرهای Pause/Mode و بررسی هویت مخزن استفاده می‌شود. `GITHUB_TOKEN` برای عملیات workflow/controller جدا باقی می‌ماند. cycle controller پیش از اجرای Codex این secret را از environment حذف می‌کند.
- نبودن secret یا هر خطای مجوز/اعتبارسنجی، اجرای task را متوقف می‌کند. مقدار token در log یا فایل ذخیره نمی‌شود.
- پیش‌نیاز مالک: secret را در GitHub → Settings → Secrets and variables → Actions با نام دقیق `AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN` ایجاد کند. مقدار token نباید در ChatGPT، کد یا issue ارسال شود.
- exact-head CI و آزمایش خواندن واقعی متغیرها روی Runner هدف همچنان pending است. تا تکمیل این موارد هیچ merge یا ادعای پذیرش مجاز نیست.

---

## 2026-11-24 — اصلاح مجوز خواندن زندهٔ Pause و Execution Mode

- مبنای فعلی بررسی‌شدهٔ `main`: `ff90d2e3745cb8b2d8dfb08d7b9baddef903d1f1`.
- PRهای مرتبط در زمان این snapshot: #863، #866، #869 و #870 باز هستند؛ #864 و #868 بسته شده‌اند. وضعیت بسته‌شدن به‌تنهایی به‌عنوان ادغام یا پذیرش عملیاتی تفسیر نمی‌شود.
- نقص جدید در stack مربوط به #866/#870: مرحلهٔ live gate از `github.token` برای خواندن `/actions/variables/{name}` استفاده می‌کرد؛ اما فهرست رسمی permissionهای `GITHUB_TOKEN` شامل `Variables: read` نیست. API خواندن متغیرهای repository به این permission نیاز دارد؛ بنابراین این مسیر نمی‌تواند مبنای قابل اتکای live Pause/Mode باشد.
- اصلاح در branch `fix/recovered-pr-chain-live-pause-gate-20261124`: مرحلهٔ post-concurrency اکنون credential احرازشدهٔ GitHub CLI حساب سرویس Runner را فقط برای GET هویت repository و متغیرهای Actions به‌کار می‌برد. `GITHUB_TOKEN` برای عملیات workflow/controller جدا می‌ماند. نبودن احراز هویت یا مجوز خواندن متغیرها fail-closed است.
- regression contract تست، منبع token را از `gh auth token --hostname github.com` الزام می‌کند و استفاده از `github.token` در Authorization این gate را رد می‌کند.
- exact-head CI و تأیید روی Runner واقعی برای `gh auth token` و خواندن دو متغیر هنوز pending است؛ تا آن زمان PR #870 Draft می‌ماند و هیچ merge مجاز نیست. هیچ workflow یا task واقعی اجرا نشده و هیچ متغیر Pause/Mode، task، checkpoint، PR، Runner، ACL، database یا دادهٔ کاربر تغییر نکرده است.

---

## 2026-11-24 — وضعیت زندهٔ Autonomous Engineering و اصلاحات در حال اعتبارسنجی

- SHA فعلی `main`: `3d1e843ff1d2726e74e16535cdff3ee3c1da44ea`.
- PRهای مرتبط:
  - [#863](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/863) — اصلاح startup و Windows State Broker؛ Draft، head `760a32bfc35f26698af0f7af74f06b121288d782`. CI exact-head در حال اجراست؛ CodeQL و Dependency Audit موفق‌اند.
  - [#864](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/864) — سریالی‌سازی refresh وضعیت UI بعد از mutation؛ Draft، head `8303afa5d44f104c6e9a5d569868f3db248023b0`. تمام workflowهای گزارش‌شده موفق‌اند.
  - [#865](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/865) — Draft قدیمی قفل Elevated؛ بسته شد و diff باقی‌مانده بدون حذف تغییرات به [#868](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/868) منتقل شد.
  - [#866](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/866) — خواندن زندهٔ Pause و Execution Mode پس از گرفتن concurrency slot؛ Draft، شاخهٔ `fix/autonomous-engineering-live-pause-gate-20261124`. سه assertion قدیمی در تست‌ها اصلاح شده‌اند؛ exact-head CI جدید در صف است.
  - [#867](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/867) — انتخاب job واقعی Codex برای لغو؛ Draft، head `7238ea3da83678a06370160af9183d34ad4a6421`. تست‌ها، Browser E2E، CodeQL و Dependency Audit موفق‌اند.
  - [#868](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/868) — قفل machine-wide در پردازهٔ Elevated و آزمون سناریوی orphaned child پس از timeout والد؛ Draft، head `ef7bc25cdc79ba4adb9d3bfc3848dee2a59496a4`، بر پایهٔ head جاری #863. Dependency Audit موفق؛ تست‌ها و آزمون‌های Windows در حال اجرا/صف هستند.
- نقص Elevated setup: قفل Python والد به‌تنهایی پردازهٔ UAC-elevated فرزند را پوشش نمی‌دهد. #868 mutex را پیش از هر بارگذاری ماژول/کشف Runner/تغییر ACL یا سرویس می‌گیرد. آزمون Windows، والد را terminate می‌کند و بررسی می‌کند فرزند همچنان قفل را نگه می‌دارد؛ abandoned owner بدون تأیید صریح اپراتور fail-closed می‌ماند.
- نقص Pause: خواندن snapshot متغیر GitHub پیش از concurrency برای job صف‌شده قابل اتکا نبود. #866 پس از گرفتن slot، مقدار زندهٔ Pause و Mode را از API می‌خواند؛ پیش از default کردن 404 متغیر، دسترسی و هویت repository را با همان token تأیید می‌کند تا 404 ناشی از دسترسی نامعتبر به مخزن fail-open نشود. خطاهای دیگر و مقدار نامعتبر fail-closed هستند. Recovery policy نیز قبل و بعد از backoff تنظیمات زنده را بازخوانی می‌کند.
- نقص Cancel: انتخاب صرفاً بر اساس وضعیت workflow می‌توانست اجرای تازه‌ای را که هنوز پشت concurrency منتظر است لغو کند. #867 وضعیت job به نام `codex` را بررسی می‌کند و اجرای واقعی را بر اجرای صرفاً صف‌شده مقدم می‌داند.
- اصلاحات Windows CI در base #863: تست‌های PATH PowerShell از registry واقعی runner جدا شدند؛ assertion هشدار rollback با متن واقعی هماهنگ شد؛ مقایسهٔ ACL فقط bit سیستم‌عامل `D:AI` را normalize می‌کند و owner/group/ACEها را دقیق مقایسه می‌کند.
- هیچ PRی merge نشده است. هیچ workflow اجرایی dispatch نشده و هیچ متغیر Pause، task، owner metadata، checkpoint، database، ACL، Runner registration یا دادهٔ پایدار روی میزبان کاربر تغییر نکرده است.
- گیت‌های باز: exact-head CI تمام PRها، Windows target-host UAC/mutex، pause/resume/cancel واقعی، Runner موجود/ناموجود، Ollama و Codex واقعی، crash/restart و بازیابی checkpoint، و بازبینی نهایی امنیت/دسترسی. PRها تا تکمیل شواهد Draft باقی می‌مانند.


---

## 2026-11-23 — snapshot زنده و ادامهٔ مهندسی Autonomous Engineering

- main با مقایسهٔ مستقیم روی SHA 31349a99b197fe702e11e8a5dd53d52ba924575c تأیید شد؛ این snapshot جایگزین وضعیت‌های تاریخی قدیمی‌تر در ادامهٔ فایل است.
- PRهای باز: #844، #845 و #846؛ هر سه Draft هستند. هیچ PRی در این مرحله merge نشده و هیچ branchی حذف نشده است.
- #844: branch refactor/portable-host-discovery-20261123 هنوز از base قدیمی 481abd62b90a4fd1f209e6015d476225dbfbcc منشعب است و کامل نیست؛ فقط پس از بررسی و rebase مستقل قابل تعیین تکلیف است.
- #845: اصلاح DELETE Ollama و recovery diagnostics؛ CI قبلی روی head 794e9583de3978268d050d74f46353a922472f5d سبز بوده، اما PR همچنان Draft است و ادغام آن به پذیرش هدف وابسته است.
- #846: اصلاحات Broker مستقل از Runner، startup محدود UI، correlation از مرورگر تا task، و regressionهای Codex/browser در branch fix/autonomous-engineering-core-broker-bootstrap-20261123 انجام شده‌اند. code head پیش از این snapshot 94306bf381e169574f56d8012c448e3c32b0465a است؛ exact-head CI کامل هنوز اثبات نشده است.
- در CIهای قبلی #846، یک assertion قدیمی Windows هنوز متغیر $runner.Service.StartName را انتظار داشت؛ assertion به کشف هویت از $candidate.StartName اصلاح شد. Browser E2E نیز به‌درستی شناسهٔ correlation را به پیام موفقیت Resume اضافه کرد، اما تست قدیمی متن را با برابری کامل می‌سنجید؛ تست اصلاح شد تا شناسه را نیز اعتبارسنجی کند. هیچ‌یک از این runهای قدیمی به‌عنوان CI سبز head نهایی شمرده نمی‌شوند.
- وضعیت اصلاحات #846:
  - Broker هسته‌ای بدون Runner قابل نصب است؛ اگر Runner موجود باشد هویت آن از Win32_Service.StartName کشف می‌شود و فایل .runner خوانده نمی‌شود.
  - تمام Runner listenerهای کشف‌شده پیش از ACL reconciliation متوقف و پس از آن در finally بازیابی می‌شوند؛ worker فعال باعث توقف امن عملیات می‌شود.
  - UI برای هر درخواست شناسهٔ امن ۳۲ کاراکتری می‌فرستد؛ timeout و خطای شبکه نیز همان شناسه را به کاربر نشان می‌دهند.
  - اگر Broker یا Ollama آماده نباشند، اجرای عادی باید UI را در حالت محدود بالا بیاورد؛ --check-only همچنان کد خروج غیرصفر می‌دهد. شکست نصب وابستگی‌های Python همچنان مانع اجرا است.
  - ADR-016، راهنمای عملیات، inventory UI، UI acceptance و engineering log در branch به‌روزرسانی شده‌اند.
- Issues #769، #290 و #52 باز هستند. #769 برای ممیزی branchهای قدیمی حفظ می‌شود؛ هیچ branch قدیمی یا دادهٔ durable حذف نشده است.
- شواهد هنوز لازم: exact-head tests/Windows full suite/storage/Browser E2E/CodeQL/dependency audit؛ سپس تست روی میزبان واقعی Windows با حساب غیرادمین UI، Runner موجود با .runner محدود، نبود Runner، Ollama/Codex واقعی، pause/resume/cancel، crash/restart و حفظ checkpoint.
- هیچ task، owner metadata، checkpoint، database، ACL یا Runner registration روی سیستم محلی کاربر در این مرحله تغییر نکرده است. PR #846 تا تکمیل CI و پذیرش میزبان Draft می‌ماند.

---

## 2026-11-22 — وضعیت جاری پس از تعیین تکلیف PRها

- main روی 9d97d2e3fa8f72429e6bb5510192ba70a6d376d8 است. PR #837 از head 80c126734123a5e84596d320ff625278495764e0 با squash ادغام شد؛ Linux 2511/14/69، Windows 2509/16/69، Windows storage، Browser/dashboard smoke، real Docker sandbox، CodeQL و dependency audit موفق.
- #819 و #821–#826 به‌عنوان sourceهای #837 بسته شدند؛ #820/#834 با حفظ تغییرات UI در #836 بسته شدند؛ #833/#835 به‌دلیل شکست lifecycle tests بسته شدند؛ #828/#829/#831/#832 نیز بسته‌اند. هیچ branchی حذف نشده است.
- تنها PR باز #836 است؛ Draft. head قبلی fc8b039dce7c8ea642ee850a5e0de99ecd8fefc5 تمام checks را گذراند: Linux 2491/14/77 و Windows 2489/16/77، به‌علاوهٔ Browser E2E، Windows storage، CodeQL، Analyze و pip-audit. head جدید پس از sync main و اصلاح timeout/trace نیازمند CI مجدد است.
- Gate A inventory و state-transition mapping ثبت شده؛ Gate B #837 در main و #836 cleanup lock/parent guard را اضافه می‌کند. correlation propagation، target-host Windows UI/Runner/Ollama/Codex، crash/restart، restore/rollback و owner acceptance باز هستند.
- هیچ task، checkpoint، database، ACL یا durable state حذف/بازنویسی نشده است. workflow فعال main 44995223588 دست‌نخورده باقی ماند. PR #836 تا exact-head CI و target-host/owner acceptance در Draft می‌ماند.

---

# وضعیت یکپارچهٔ پروژه

## Snapshot زنده — 2026-11-22

- `main` فعلی: `0367a3ebfd92ac196bb096f1a45cbfed5a61985d`.
- PR #799 (lease epoch و owner+epoch fencing) ادغام شده است؛ commit ادغام: `852423ae8b862b834e007eada0033d087a37cd83`.
- PR #817 (structured API errors) ادغام شده است.
- PR #827 (بازیابی گزارش Codex از JSONL معتبر) ادغام شده است؛ PR قدیمی #818 که با main تعارض داشت، با توضیح superseded بسته شد.
- روی SHA فعلی main، tests، browser-e2e، dependency-audit و CodeQL همگی completed/success هستند: [tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44979290739)، [browser-e2e](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44979290773)، [dependency-audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44979290835)، [CodeQL](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44979290781).
- اجرای workflow با نام `Autonomous local Ollama engineering` در [run 44979290758](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44979290758) موفق تمام شد، اما job اجرای Codex به‌علت pause gate اجرا نشد؛ بنابراین این run، تست واقعی Codex/Ollama یا پذیرش Windows target نیست. workflow recovery نیز skipped بود.
- PR #834 برای UI/cleanup ماژول Autonomous Engineering در حالت Draft نگه داشته شده است؛ inventory و state-transition matrix در `docs/UI_AUTONOMOUS_ENGINEERING_INVENTORY_2026-11-22.fa.md` ثبت شده است. وابستگی runtime اپلیکیشن به namespace غیرقابل‌نصب `scripts` حذف شده و lock helperهای مشترک به بستهٔ `app` منتقل شده‌اند؛ فایل‌های CLI فقط compatibility wrapper هستند و build configuration تغییر نکرده است. CI exact-head جدید در صف اجراست؛ target-host acceptance همچنان pending است.
- PR #833 کنترل‌های TaskRuntime برای سایر ماژول‌ها را در حالت Draft نگه داشته‌ایم؛ طبق اولویت master prompt، تا تکمیل Gateهای Autonomous Engineering نباید آن را merge یا جلوتر برد.
- PRهای #819 تا #826 هنوز باز هستند و base آن‌ها SHA قدیمی `91b7e062e21d06d774c2f1f394bc72e4eb69af2c` است؛ موفقیت CI روی head قدیمی به main فعلی قابل تعمیم نیست. پیش از هر merge باید rebase/revalidation و review مستقل انجام شود.
- وضعیت Gateهای A–J در inventory مورخ 2026-11-22 آمده است. Gate B یک race مهم cleanup را در PR #834 هدف گرفته: cleanup زیر repository execution lock و با recheck دوبارهٔ GitHub runها انجام می‌شود. این اصلاح هنوز CI نشده است.
- Windows target، ACL واقعی دو حساب، اجرای واقعی Ollama/Codex، crash/restart، restore/rollback و پذیرش مالک هنوز pending هستند. هیچ migration روی دیتابیس کاربر اجرا نشده و هیچ task/checkpoint کاربر تغییر داده نشده است.

## Historical snapshot — 2026-11-21

## تاریخی — snapshot مورخ 2026-11-21 دربارهٔ PR #799 (بعداً ادغام شد)

- در زمان این snapshot، شاخه از main با SHA `bc1d273bdece690b5067a70b2deea8aa77ed7e28` ساخته و PR [#799](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/799) به‌صورت Draft باز بود؛ PR در 2026-11-21 با merge commit `852423ae8b862b834e007eada0033d087a37cd83` ادغام شد.
- علت ریشه‌ای: مالکیت task فقط با رشتهٔ قابل‌بازیافت `lease_owner` و زمان انقضا سنجیده می‌شد؛ worker قدیمی با شناسهٔ تکراری می‌توانست پس از reclaim هنوز mutation انجام دهد. همچنین task manager مسئولیت‌های storage، lease، control و قرارداد داده را در یک فایل ۶۴۵ خطی جمع کرده بود.
- اصلاح در PR: ماژول‌های جدا برای قرارداد، ذخیره‌سازی، چرخهٔ lease و کنترل؛ facade سازگار `app.task_manager`؛ migration افزایشی ۴۸ برای `lease_epoch`؛ افزایش epoch هنگام claim در تراکنش؛ اعتبارسنجی owner+epoch+expiry برای mutation؛ توکن یکتای هر نمونهٔ worker؛ انتقال epoch در TaskContext.
- تست‌های regression برای reclaim با worker label یکسان، رد mutation با epoch قدیمی، یکتایی توکن worker و بازیابی checkpoint اضافه شده‌اند.
- وضعیت CI exact-head: هنوز اجرا/نتیجه‌ای از GitHub Actions در دسترس نیست؛ هیچ موفقیتی ادعا نمی‌شود.
- دامنهٔ باز: fencing در تراکنش‌های دامنه‌ای learning job/method/quality gate هنوز باید به تمام مسیرهای mutation متصل شود؛ UI fault isolation و اجرای کامل ماژول‌ها نیز همچنان باز است.
- تست میزبان Windows، crash/restart واقعی، soak، restore/rollback و بازبینی امنیتی مستقل در آن snapshot انجام‌شده محسوب نمی‌شدند. merge بعدی PR #799 به معنی تکمیل این پذیرش‌های عملیاتی نیست؛ هیچ migration روی دیتابیس کاربر اجرا نشده است.


## 2026-11-15 — اصلاح طبقه‌بندی شکست و بازیابی چرخهٔ خودکار

- وضعیت: **پیاده‌سازی کامل در PRهای باز؛ CI دقیق هر دو PR سبز؛ ادغام‌نشده؛ تست محلی مالک و پذیرش زنده pending**.
- مبنای main هنگام شروع این اصلاح: `f5cfa837e788ccfa84e13203fb33ac1b1937927a`.
- اجرای [43695676192](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43695676192) در `schedule-gate` موفق بود، اما در مرحلهٔ `Persist manual task under the controller's runner and concurrency ownership` با پیام `Another manual task is still pending` متوقف شد. مرحلهٔ اجرای کنترل‌کننده اصلاً اجرا نشد؛ بنابراین این رخداد شکست اجرای task نبود.
- recovery قبلی همهٔ شکست‌های Workflow را بدون بررسی job/step در streak جمع می‌کرد و شکست پذیرش task را به‌اشتباه شکست اجرای autonomous محسوب کرد. همچنین Circuit Breaker با exit code صفر بسته می‌شد و GitHub آن را سبز نشان می‌داد، درحالی‌که dispatch انجام نشده بود: [run 43695720003](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43695720003).
- PR [#761](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/761)، شاخهٔ `fix/autonomous-recovery-failure-classification-20261115`: مرز شکست واقعی اجرای task اکنون فقط step دقیق `Execute one isolated task, validate, and publish for owner review` است. شکست‌های admission، schedule gate، پیش‌نیاز، نصب، sandbox، pause و postcondition شمارندهٔ اجرای task را افزایش نمی‌دهند و dispatch تکراری ایجاد نمی‌کنند؛ دادهٔ ناشناخته fail-closed است.
- نقص داشبورد نیز رفع شده است: ارسال task دستی جدید در صورت وجود task/owner/PR/cleanup پایدار یا عدم امکان تأیید state و GitHub رد می‌شود. مسیر dispatch خالی برای resume/reconcile کار پایدار قبلی باز است. متن task خوانده یا بازگردانده نمی‌شود.
- مسیر state و قفل اکنون در بستهٔ نصب‌شدهٔ `app/autonomous_engineering_state.py` متمرکز است. داشبورد و Runner از همان resolver ماشین‌محور و primitive قفل استفاده می‌کنند؛ چهار فایل مشترک در PR #760 و #761 یکسان هستند و ترتیب ادغام آن‌ها رفتار قفل را تغییر نمی‌دهد.
- PR #761 روی کد head `d976b639314bfe1bd4da74d8f316e30ba274eacc`: **۲۳۰۲ passed، ۱۳ skipped، ۵۱ deselected**؛ Windows storage، Browser E2E، Dependency Audit، CodeQL، تولید artifact، Markdown links، import-cycle، retrieval و claim-to-test benchmark همگی موفق شدند. اجراها: tests/Windows [43703252064](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43703252064)، Browser [43703252121](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43703252121)، CodeQL [43703252128](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43703252128)، Dependency [43703252113](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43703252113).
- PR [#760](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/760)، head `82d02119191b99d0ba071e8fd6f08841db2c7d7b): **۲۲۵۷ passed، ۱۳ skipped، ۵۱ deselected**؛ Windows storage، Browser E2E، Dependency Audit و CodeQL همگی موفق شدند.
- هر دو PR هنوز Draft و ادغام‌نشده‌اند. هیچ recovery dispatch یا اجرای autonomous زنده انجام نشده و فایل task، owner metadata، checkpoint، Pause، ACL و هویت Runner دست‌نخورده باقی مانده‌اند.
- گیت‌های عملیاتی هدف هنوز pending هستند: اجرای کامل suite روی Windows هدف، بررسی دسترسی مؤثر دو حساب، sandbox غیرادمین، crash/restart، اجرای واقعی Ollama/Codex و پذیرش مالک. چون task پایدار قبلی هنوز وجود دارد، تست زندهٔ بعدی باید با dispatch خالی برای resume همان task انجام شود؛ task دستی جدید تا تعیین‌تکلیف قبلی به‌درستی پذیرفته نمی‌شود.
- بازاعتبارسنجی snapshot مستندات PR #761 در head `e61f3053f074f46cc920ac2c75e0c877b6785928`: ۲۳۰۲ تست موفق و تمام گیت‌های GitHub سبز؛ PR #760 در head `72146234785a34a6e11c15177a17a088a615e0c3`: ۲۲۵۷ تست موفق و تمام گیت‌های GitHub سبز. Engineering Log، Project Status و Delivery Estimate سپس فقط برای ثبت شواهد نهایی به‌روزرسانی شدند؛ این تغییرات هیچ کد اجرایی یا state عملیاتی را تغییر نداده‌اند.

## وضعیت جاری — مالکیت پردازه و بازیابی Codex

- مبنای زندهٔ `main` در آغاز این کار: `1969bcc83294689efaad091ea5aaa7582c44f618`.
- PR [#719](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/719) در 2026-11-12 ادغام شد؛ commit ادغام `f87e15b9eb76f7395ea4380382d95f1bc64e21ed`. تفکیک قفل worktree، watcher checkpoint، هویت پردازه، mutex وضعیت durable، GitHub transport و چرخهٔ PR در main موجود است.
- PR [#757](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/757) به‌صورت Draft باز است: نظارت بر چرخهٔ عمر Codex، اعتبارسنجی زنجیرهٔ مالک با PID و زمان ایجاد پردازه، Job Object ویندوز و process group در POSIX تحت مالکیت progress controller (assignment پیش از resume یا session گروهی پیش از اجرا، پاک‌سازی فوری در خروج supervisor و kill-on-close/owner-chain cleanup هنگام خروج owner)، انتظار برای drain کامل پیش از شروع retry، timeout مستقل برای smoke و task، retry محدود و deadline مستقل برای فرمان‌های کنترل‌کننده.
- دو ریشهٔ مستقلِ طولانی‌شدن اجرا ثبت شدند: Codex می‌توانست از مالک خود زنده بماند؛ و حلقهٔ `while True` کنترل‌کننده پس از هر شکست، نشست جدیدی برای همان task شروع می‌کرد تا سقف شش‌ساعتهٔ GitHub Actions. subprocessهای تست/اعتبارسنجی نیز timeout مستقل نداشتند.
- پروفایل Codex در PR #757: smoke حداکثر ۵ دقیقه، idle دو دقیقه و بدون retry؛ task حداکثر ۳۰ دقیقه، idle پنج دقیقه، دو retry جریان و یک retry درخواست. سیاست جدید controller حداکثر ۲ تلاش و ۲ ساعت برای هر task در هر run است؛ پس از آن task/checkpoint حفظ و به recovery بعدی واگذار می‌شود.
- فرمان‌های کنترل‌کننده به‌طور پیش‌فرض ۵ دقیقه، مجموعهٔ کامل pytest پانزده دقیقه و انتشار/اعتبارسنجی PR بیست‌وپنج دقیقه سقف دارند. همهٔ این فرمان‌ها اکنون از process-tree supervisor عبور می‌کنند: Windows Job Object با انتظار برای signaled/empty شدن کامل job، و POSIX process group با انتظار برای خروج همهٔ اعضای زنده و escalation از SIGTERM به SIGKILL؛ فرمان تا تأیید پایان فرزندان برنمی‌گردد و قفل مخزن زودتر آزاد نمی‌شود. شکست assignment پردازهٔ suspended نیز با kill و reap صریح بسته می‌شود. ADR-020 مالکیت پردازه و ADR-021 سیاست retry و deadlineها را تعریف می‌کنند.
- طبق مستندات رسمی Codex، `unelevated` محدودیت شبکهٔ ضعیف‌تر و نبود deny-read-path دارد؛ بنابراین فقط با متغیر صریح `AUTONOMOUS_ENGINEERING_ALLOW_UNELEVATED_SANDBOX=true` قابل انتخاب است و پیش‌فرض آن غیرفعال است. MXC تنها backend خودکار است و در شکست آن، workflow به‌صورت امن متوقف می‌شود.
- CI قبلی روی SHA `a54b02e1e2b148bd7c03cd3831c4eb606358fa85` شامل ۲۲۱۲ تست موفق و یک شکست ناشی از assertion قدیمی متن خطا بود؛ قرارداد تست اصلاح و تست مثبت backend محدود `unelevated` افزوده شد. **نتیجهٔ exact-head commit نهایی هنوز باید از GitHub خوانده شود.**
- اجرای ناموفق [run 43592197032](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43592197032) روی main و SHA `1969bcc83294689efaad091ea5aaa7582c44f618` به‌علت `open sandbox setup mutex` شکست خورد؛ این اجرا از کد PR استفاده نمی‌کند و اثبات‌کنندهٔ اصلاح نیست.
- آخرین CI سبز روی SHA قبلی `41aeb6af8fbae792d1bebc577f526d420fb86f8b` بود؛ این نتیجه برای اصلاحات جدید معتبر نیست. آخرین head فعلی PR #757 برابر `89121e7b7c7395da654225e7f6b35a0f4db0429e` است و پنج بررسی exact-head (tests، Windows storage، CodeQL، pip-audit و dashboard smoke) در صف/در حال اجرا هستند؛ تا تکمیل آن‌ها هیچ نتیجه‌ای ادعا نمی‌شود.
- inventory گیت‌های باز از Issues: #290 ردیاب پذیرش کل پروژه و UI؛ #52 نیازمند بازبینی امنیتی مستقل. پس از بستن گیت مالکیت پردازه و CI، این دو گیت در اولویت‌های بعدی باقی می‌مانند.
- آخرین مقدار ثبت‌شدهٔ `AUTONOMOUS_ENGINEERING_PAUSED` برابر `true` است؛ این اصلاح آن را تغییر نداده است. تست محلی میزبان مقصد، soak، crash/restart واقعی و بازبینی امنیتی مستقل همچنان pending هستند.

> تاریخ بازبینی: 2026-11-14. وضعیت جاری در ابتدای سند ثبت شده است؛ یادداشت‌های پایین‌تر snapshotهای تاریخی هستند.

## وضعیت تاریخی اتوماسیون — PR #702 (snapshot قدیمی)

- مبنای فعلی `main`: `a01e414ea3124213bc4d06496f55c74ad6ef9ef4`. PR [#701](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/701) در همین SHA ادغام شده است؛ اصلاحات حفظ وظیفهٔ دستی، checkpoint، recovery، هم‌ترازی داشبورد و ترتیب persistence پیش از schedule gate اکنون بخشی از main هستند.
- PR [#702](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/702) روی شاخهٔ `fix/automation-codex-context-smoke-20261107` باز و هنوز ادغام‌نشده است. این PR برای رفع دو گیت باقیماندهٔ Codex/Ollama روی Windows Runner است.
- شواهد آخرین اجرای ناموفق پیش از اصلاحات: [run 42355938096](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/42355938096). حساب غیرمدیر Runner توانست MXC، اجرای مستقیم `cmd.exe` و Python را با markerهای `MXC_SERVICE_OK` و `MXC_CMD_PYTHON_OK` اجرا کند؛ اما اجرای واقعی Codex پنج command event داشت، هیچ agent message نساخت و smoke gate شکست خورد. این اجرا موفق محسوب نمی‌شود.
- ریشهٔ Context: preflight قبلی فقط در درخواست warm-up مقدار `options.num_ctx=64000` را می‌فرستاد. درخواست‌های مستقل Codex از مدل پایه دوباره با مقدار پیش‌فرض ۸۱۹۲ بارگذاری می‌شدند. سقف Context مدل در metadata، Context تخصیص‌یافتهٔ runtime را اثبات نمی‌کند.
- ریشهٔ آزمون JSONL: parser قبلی به دقیقاً یک فرمان مدل‌تولیدشده وابسته بود و فرمان را فقط از completion event می‌خواند. این قرارداد نسبت به تعداد tool callهای مدل و تفاوت فیلدهای start/completion شکننده بود.
- اصلاحات PR #702:
  - ساخت مدل محلی جداگانهٔ `<selected-model>-codex-64k` از مدل انتخاب‌شده با پارامتر پایدار `PARAMETER num_ctx 64000` از طریق Ollama API؛ مدل اصلی و تنظیمات سرور تغییر نمی‌کنند.
  - تأیید پارامتر ذخیره‌شده با `/api/show` و Context تخصیص‌یافته با `/api/ps`، پیش از Codex و دوباره پس از اجرای Codex.
  - استفاده از مدل مشتق‌شده در هر دو Workflow تشخیصی و مهندسی خودکار.
  - هم‌بسته‌سازی command start/completion با item ID؛ پذیرش چند فرمان فقط وقتی همه موفق باشند و دقیقاً یک فرمان، عملیات کامل ایجاد/خواندن/حذف فایل و marker را اثبات کند.
- پیام متنی نهایی مدل شرط قبولی smoke تشخیصی نیست؛ turn باید کامل و بدون خطا باشد و شواهد واقعی اجرای فرمان معتبر باشند. الزام marker نهایی task در چرخهٔ مهندسی خودکار و کنترل PR/merge مالک دست‌نخورده باقی می‌ماند.
  - حذف متن خام فرمان، خروجی و پیام عامل از evidence JSON؛ ثبت فقط شواهد محدود و غیرمحتوایی.
  - افزودن regression برای چند فرمان، completion فاقد متن فرمان، تطبیق ID، شکست اجرا، redaction و تثبیت Context.
- **اعتبارسنجی PR #702:** اجرای exact-head CI برای آخرین SHA و diagnostic واقعی self-hosted Windows باید پس از آخرین تغییرات تکمیل شوند. نتایج چهار Check روی SHA قدیمی‌تر `42fdec3909ba37b68a3e3a1ed47bf9776421ff9c` به‌عنوان تأیید این اصلاحات محسوب نمی‌شوند.
- **گیت‌های باز:** موفقیت Codex/MXC روی حساب غیرمدیر و Context واقعی ۶۴k روی Runner؛ soak و مصرف حافظه روی میزبان مقصد؛ اجرای کامل autonomous cycle؛ crash/restart recovery؛ staging restore/rollback؛ تأیید مالک. Issue [#290](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/290) همچنان tracker پذیرش کل پروژه است و Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) به بازبینی امنیتی مستقل نیاز دارد.
- سیاست ادغام: PR #702 تا سبزشدن تمام Workflowهای لازم روی SHA نهایی، موفقیت diagnostic واقعی Windows و تأیید محلی مالک ادغام نمی‌شود. هیچ merge خودکار، bypass، اجرای unsandboxed یا تغییر سطح دسترسی Runner مجاز نیست.

## وضعیت کنسول مهندسی خودکار — جزئیات عملکرد

- رابط عملیات محلی، readiness diagnostics، تنظیم مدل و interval، اجرای وظیفهٔ دستی، pause/resume/cancel، نمایش زندهٔ plan و phase، و نمودار پیشرفت مبتنی بر scorecard در PR #680 پیاده‌سازی شده‌اند.
- حالت پیوسته پس از تغییر main یا heartbeat ساعتی خودکار شروع می‌شود؛ هر وظیفه پس از تست به PR باز برای بازبینی مالک تبدیل می‌شود. ادغام فقط دستی است و ادامهٔ صف پس از تأیید SHA دقیق و CI پس از ادغام انجام می‌شود.
- PR #680 و PR #689 اکنون در main ادغام شده‌اند. PR #701 اصلاحات durability و Sandbox را ادامه می‌دهد؛ وضعیت جاری و گیت‌های باز در ابتدای همین سند ثبت شده‌اند.


## 1. خلاصهٔ وضعیت

پروژه قابل اجرا و در حال توسعه است، اما برای بهره‌برداری حساس یا مستقل هنوز تأیید نهایی نشده است. قابلیت‌های تحقیق منبع‌محور، حافظهٔ تجربه، یادگیری مسیرمحور، صف پایدار وظایف، sandbox و گردش‌کار خودبهبوددهی کنترل‌شده وجود دارند؛ با این حال ارزیابی جامع مستقل کیفیت دانش، پذیرش عملیاتی روی میزبان مقصد، و برخی دروازه‌های امنیتی و بازیابی همچنان باز هستند.

**مبنای وضعیت:** PR #664، #667، #668 و #669 قبلاً مستندات، صف پایدار یادگیری، لاگ مهندسی، scorecard و کنترل تشخیص زبان را به main افزودند. PR #671 نیز ذخیرهٔ exception خام و diagnostics کنترل‌نشده در کشف منابع ابری را حذف کرد و با squash commit `2a5c83bf0ba924f4a2c8f206338a04872562d72d` ادغام شد. Exact-head CI برای PR #671 موفق است؛ post-merge tests، dashboard smoke، dependency audit و Windows storage روی SHA فعلی همگی موفق شده‌اند. Issue #52، پذیرش میزبان هدف، staging restore/rollback و ارزیابی زنده همچنان دروازه‌های باز هستند.

## وضعیت فعلی یادگیری و ادامهٔ تکمیل — پس از PR #710

- PR #710 در 2026-11-11 با squash در `main` ادغام شد؛ merge commit: `9e198a0640593bcc3892f3a97aa8956ba522653c`. CI دقیق head پیش از ادغام: tests **2028 passed / 50 deselected**، Windows storage **11 passed / 47 deselected**، browser E2E و dependency audit موفق. این نتیجه فقط همان commit و دامنهٔ همان workflowها را پوشش می‌دهد.
- PR [#711](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/711) در 2026-11-11 با squash در `main` ادغام شد؛ merge commit: `02006e2b82f800c07b50ad5e849093ad07786f60`. شاخهٔ `feature/universal-learning-contract-v2` اکنون تکمیل و ادغام شده است.
- پرامپت Master Engineering تکمیل شده است تا موضوع کاربر به‌تنهایی محدودهٔ syllabus باشد؛ مثال‌های کاربر حق محدودکردن سرفصل‌ها را ندارند. برای هر موضوع، نقشهٔ جامع، مدل chronology واقعی، release ledger، ثبت تاریخ انتشار/سال/نسخه، ردیابی منبع در سطح ادعا/درس، و پایش patch/hotfix/security/errata/deprecation/EOL الزام شده است.
- سیاست ادغام prompt اصلاح شده: تست‌های خودکار قابل اجرا روی exact SHA gate هستند؛ تست‌های local/target-host/human که در دسترس نیستند باید PENDING/NOT RUN بمانند و مانع ادامهٔ کار یا ادغام خودکار نشوند، مگر آنکه branch protection یا بررسی امنیتی مستقل صراحتاً آن‌ها را gate کرده باشد. هیچ تست انجام‌نشده‌ای موفق گزارش نمی‌شود.
- در `app/learning_curriculum_tasks.py` شواهد منابع به صورت round-robin در batchهای 8تایی به مدل ارائه می‌شوند. اگر مدل زودتر `has_more=false` بدهد، task تا بررسی تمام منابع ذخیره‌شده ادامه پیدا می‌کند؛ batch آخر می‌تواند بدون افزودن عنوان تکراری، پایان بررسی شواهد را ثبت کند.
- سقف ثابت 10 URL برای هر موضوع، 30 نسخه/سال در metadata و 40 حوزهٔ پوشش حذف شده است؛ اندازهٔ پاسخ JSON و batch فقط محدودیت هر درخواست هستند. تعداد کل سرفصل‌ها و منابع با checkpoint ادامه‌پذیر است.
- `web_sources.discover_sources` اکنون تا 30 نتیجه در هر درخواست fetch می‌کند (حد provider per-request)، و مسیر curriculum برای هر query این ظرفیت را درخواست می‌کند. تعداد کل منابع/بچ‌ها به این مقدار محدود نمی‌شود.
- متن کامل صفحات قابل‌استخراج در migration 46 در جدول `learning_research_source_text` نگهداری می‌شود؛ فقط metadata و excerpt کوتاه در checkpointهای تکراری می‌ماند. هر منبع به قطعات ۱۰۰۰ نویسه‌ای تقسیم می‌شود و همهٔ قطعات در batchهای ترتیبی به مدل ارائه می‌شوند. هنگام پایان task، source text به path تحقیق متصل می‌شود و متن کامل در `sources_json` تکرار نمی‌شود.
- استخراج تاریخ انتشار/به‌روزرسانی از metadataهای رایج HTML اضافه شده است؛ نبود metadata همچنان unknown می‌ماند. آزمون‌های رگرسیون برای حفظ متن کامل، chunking، استخراج تاریخ، پوشش تمام منابع در چند batch، 103 منبع در 13 batch، release history بیش از 30 مورد، و retrieval بیش از 5 منبع اضافه شده‌اند؛ CI شاخهٔ تکمیلی هنوز باید اجرا و بررسی شود.
- subject map، release ledger، فهرست شکاف‌ها و coverage audit در migration 47 به‌صورت پایدار ذخیره و از API مسیر یادگیری قابل بازیابی هستند؛ تاریخچهٔ نسخه‌ها با تاریخ واقعی انتشار و نسخه مرتب می‌شود و داده‌های متعارض/ناقص حذف نمی‌شوند.
- پاسخ‌های دستیار از تاریخچهٔ محلی چت و تحقیق، حتی اگر کوتاه یا کم‌کیفیت باشند، به‌عنوان candidate با وضعیت unverified ثبت می‌شوند؛ پاسخ‌های دارای credential آشکار برای جلوگیری از ذخیرهٔ secret حذف می‌شوند. این مشاهده‌ها در ساخت syllabus و تولید درس مرتبط به‌عنوان دادهٔ کمکیِ غیرقابل‌اعتماد وارد prompt می‌شوند، نه دانش تأییدشده.
- اصلاح خودکار گزارش خرابی درس با ایجاد job و revision جدید انجام می‌شود؛ درس قبلی، گزارش کاربر و provenance حفظ می‌شوند. کیفیت پایین یا ارزیابی ناقص باعث حذف محتوا نمی‌شود و بازبینی معنایی مستقل از مدل تولید در صورت پیکربندی قابل اجراست.
- پایش نسخه‌ها در runtime یادگیری هر ۳۰ ثانیه اجرا می‌شود؛ حداکثر سه مسیر due در هر چرخه پردازش می‌شوند و باقی‌مانده با زمان‌بندی پایدار در چرخه‌های بعدی ادامه می‌یابد. تغییر release fingerprint یک پژوهش افزایشی جدید ایجاد می‌کند و مسیر قدیمی حفظ می‌شود.
- تنظیمات دائمی و موقت provider/model برای laneهای موجود از API و رابط تنظیمات پشتیبانی می‌شود؛ Ollama و endpointهای محلی/راه‌دور OpenAI-compatible قابل انتخاب‌اند. مدل پیش‌فرض learning برابر `gpt-oss:20b` است؛ اجرای واقعی آن روی محیط میزبان مقصد هنوز انجام‌شده ادعا نمی‌شود.
- آخرین اعتبارسنجی exact-head پیش از ادغام روی SHA `cd0f7242d44b51b2b5b76c29e4f1abd39409bee0`: tests **2045 passed / 50 deselected**، Windows storage **11 passed / 47 deselected**، Browser E2E موفق و dependency audit موفق. همین SHA با squash در merge commit بالا وارد `main` شد. وجود فایل‌های کلیدی و migration 47 روی `main` پس از ادغام نیز بررسی شد.
- اجرای واقعی میزبان مقصد، تست انسانی و اجرای زندهٔ `gpt-oss:20b` هنوز انجام‌شده ادعا نمی‌شوند و طبق دستور کاربر merge gate نیستند؛ هر نتیجهٔ آن‌ها در گزارش به‌صورت جداگانه ثبت می‌شود.


## 1.1 تغییرات جدید پس از snapshot قبلی

- **آخرین اعتبارسنجی PR #710:** روی commit دقیق `5e58301417767124ee01b9f4e331a6fd74354525` همهٔ workflowهای GitHub موفق شدند: tests [#43001048500](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048500) با **1,980 passed / 50 deselected**، تست‌های Windows storage، بررسی artifactهای یادگیری، 94 لینک Markdown، import-cycle checks، benchmark بازیابی و claim-to-test برابر **10/10**؛ browser E2E [#43001048937](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048937)؛ dependency audit [#43001048856](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43001048856). این نتایج CI هستند؛ اجرای واقعی مدل، RAM/latency، بازیابی پس از restart زیر بار مدل و کیفیت درس فارسی/انگلیسی روی میزبان مقصد هنوز تأیید نشده‌اند. PR #710 در 2026-11-11 با commit `9e198a0640593bcc3892f3a97aa8956ba522653c` ادغام شد؛ این CI تاریخی مربوط به SHA قدیمی‌تر است و نباید به‌عنوان اعتبارسنجی PRهای بعدی استفاده شود.


- PR [#710](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/710) (جایگزین PR #709): سقف کلی سرفصل/درس/batch حذف شده است؛ batchهای checkpointشدهٔ ۸ تا ۴۰تایی صرفاً اندازهٔ هر درخواست هستند. مدل پیش‌فرض `gpt-oss:20b` است و جایگزینی خاموش مدل مجاز نیست. سرفصل هر موضوع مستقل از مثال کاربر و از نخستین نسخهٔ مستند تا جدیدترین نسخه به ترتیب سال و نسخه ساخته می‌شود؛ تاریخ انتشار از تاریخ به‌روزرسانی منبع و زمان بازیابی جدا ذخیره می‌شود. Migration 43 provenance نسخه و migration 44 پایش دوره‌ای انتشار را اضافه می‌کنند. امتیاز کیفیت مانع نگهداری درس غیرخالی نیست؛ گزارش خطای کاربر job اصلاحی جدید می‌سازد و درس قبلی را تغییر نمی‌دهد. پایش هر شش ساعت تغییر fingerprint منابع را به صف یادگیری می‌فرستد. **CI موفق قبلی فقط روی SHA قدیمی `d18fceea301c51ef56e3731b03055af446c2be04` معتبر است** و تغییرات جدید را پوشش نمی‌دهد. PR همچنان Draft و ادغام‌نشده است؛ تست محلی میزبان مقصد برای ادغام لازم است.

- PR [#641](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/641): تابع maintenance دوره‌ای AI-learning اکنون reconciliation idempotent وظایف `learning.run_job` را اجرا می‌کند تا شکست موقت enqueue در زمان اجرای برنامه بدون restart ترمیم شود. خطای reconciliation مستقل ثبت می‌شود و مانع اجرای factual/browser maintenance نمی‌شود. دو regression برای ایجاد دقیقاً یک task و ادامهٔ maintenance پس از خطای تزریق‌شده اضافه شده‌اند؛ ADR-017 قرارداد را ثبت می‌کند. commit موجود در `main`: `8d7674cde9ebb5684ab6102eecf4e0ca8a0a824d`. CI پس از ادغام این SHA هنوز مستقلاً تأیید نشده است.
- PR [#642](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/642): آزمون Chromium برای فعال‌سازی Skip Link، انتقال focus به main landmark، پیمایش صفحه‌کلید به تمام ۱۵ مقصد ناوبری و فعال‌سازی مسیر مدیریت وظایف با Enter اضافه شد. تغییر runtime ندارد. CI روی head دقیق `87d888490499c7f405f4a8798a17767837ded4df`: 1752 passed و 5 skipped؛ Windows storage، browser E2E و dependency audit موفق. ادغام در `main`: `c6a28ce21cfaa6130bed36e7fbb173e4cf39a9be`. آزمون دستی screen-reader همچنان دروازهٔ انسانی است.
- PR [#644](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/644): پنج سند اصلی پروژه با وضعیت فعلی main، شواهد دقیق CI و محدودیت‌های پذیرش همگام شدند. PR فقط مستندات را تغییر داد؛ head `ba0ed1467826f8415a815fd3fde5da85713516af` با 1752 passed/5 skipped، Windows storage، Chromium E2E، dependency audit، بررسی 84 فایل Markdown و benchmark 10/10 موفق شد. ادغام مستندات: `1348ddd3a21b9ea7e987aff929700f44ee21fbb1`؛ post-merge CI روی SHA `1348ddd` نیز با 1752 passed/5 skipped، Windows storage، browser E2E، dependency audit، 84 Markdown links و benchmark 10/10 موفق شد.
- PR #645 ادغام شد: failure isolation در reconciliation وظایف learning؛ خطای یک job رسیدگی به jobهای بعدی را متوقف نمی‌کند و شکست query اولیه با ثبت خطا به maintenance دوره‌ای واگذار می‌شود. دو regression برای خطای تزریق‌شده در سطح job و startup اضافه شد. merge commit `fe1d533eb4a8cff86de978be4487898666afdddd`; post-merge CI: 1754 passed/5 skipped، Windows storage، browser E2E، dependency audit، 84 Markdown links و benchmark 10/10 موفق.
- PR #646 ادغام شد: اعتبارسنجی دقیق `Origin` و `Sec-Fetch-Site` برای تمام مسیرهای `/api/` درخواست‌های cross-origin را پیش از رسیدن به endpoint متوقف می‌کند؛ کلاینت‌های محلی بدون metadata مرورگر حفظ شدند. هدرهای `X-Frame-Options: DENY` و `frame-ancestors 'none'` نیز clickjacking را مسدود می‌کنند. ADR-018 ثبت شد. exact-head و post-merge CI هر دو با 1758 passed/5 skipped، Windows storage، Chromium E2E، dependency audit، Markdown-link validation و benchmark 10/10 موفق شدند. merge commit `6b19b84c78e17d8b5c28122964c7130dcf42f853`.
- PR [#653](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/653): URL parserهای منابع عمومی و knowledge indexer userinfo خالی و پورت صریح خالی را رد می‌کنند؛ خطای syntax مربوط به IPv6 نامعتبر به خطای دامنه‌ای تبدیل می‌شود تا نتیجهٔ خراب، پردازش منابع معتبر بعدی را متوقف نکند. ادغام: `2208465d3b185b2d1705eb94b37cde986d21532d`; exact-head tests [#41192146128](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192146128)، browser E2E [#41192146095](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192146095) و dependency audit [#41192146031](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192146031) موفق شدند.
- PR [#656](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/656): دروازهٔ evidence درس فقط URLهای HTTPS بدون userinfo و با پورت پیش‌فرض را می‌پذیرد؛ policy version و regressionهای userinfo/port خالی به‌روزرسانی شدند. ادغام: `54a1a60af284e27d9631e1b4284ffb0f7ebc02e4`; exact-head و post-merge tests، browser E2E و dependency audit موفق شدند.
- PR [#663](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/663): URL اسکریپت داشبورد یادگیری versioned شد تا cache قدیمی مرورگر کنار گذاشته شود و regression test تضمین می‌کند checkbox انتخاب موضوع داخل عنصر `summary` قرار نگیرد. ادغام در `main`: `cc07ecb23bf0d922843b321eef0d45c0b1588d53`. CI پس از ادغام روی همان SHA با 1801 passed/5 skipped، Windows storage، Chromium E2E و dependency audit موفق شد.
- PR [#665](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/665): مسیر انتخاب تست‌های محیط‌وابسته صریح شد؛ ادغام در `main`: `a23760805310154d83e47489eddf00fc9b91c7f6`. tests [#41210701513](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41210701513) با 1800 passed و 47 deselected، Windows storage، browser E2E [#41210701574](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41210701574)، Docker sandbox [#41210701572](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41210701572) و dependency audit [#41210701463](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41210701463) موفق شدند.
- PR [#666](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/666): سند `docs/MASTER_ENGINEERING_PROMPT.md` به مخزن اضافه شد؛ ادغام در `main`: `9308c1e8e8338a1aa52f3e562481bd308d938443`.
- PR [#664](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/664): پنج سند وضعیت/پذیرش با baseline و شواهد CI همگام شدند؛ ادغام: `48aafa2a2859c74c8fe3473b8f2f1e6dfe474dfe`؛ exact-head CI موفق شد.
- PR [#667](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/667): ادامهٔ خودکار درس‌ها اکنون تمام سرفصل‌های تأییدشده را پیش از browser work در صف پایدار SQLite ثبت می‌کند؛ regression اضافه شد؛ ادغام: `6f601e8cc210419392bfbd1affb9590db978dd3a`؛ exact-head CI موفق شد (1801 passed/47 deselected). exact-head tests [#41211160528](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211160528)، browser E2E [#41211160563](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211160563) و dependency audit [#41211160694](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211160694) موفق شدند. Post-merge CI on reviewed baseline `9308c1e8e8338a1aa52f3e562481bd308d938443` passed: tests [#41211823993](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211823993) (1800 passed/47 deselected, Windows storage, 88 Markdown files and deterministic benchmarks 10/10), Chromium dashboard smoke [#41211823939](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211823939), and dependency audit [#41211823949](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41211823949).
- PR [#655](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/655): `submit_query_feedback` اکنون فقط integer واقعی `-1` یا `1` را می‌پذیرد؛ boolean، float و رشتهٔ عددی رد می‌شوند و feedback همچنان فقط سیگنال usefulness است، نه مدرک حقیقت. ادغام: `5ec2ab3484f5ba0248e9983bbb8c7fbddba34f88`; exact-head tests [#41192749574](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192749574)، browser E2E [#41192749458](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192749458) و dependency audit [#41192749513](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41192749513) موفق شدند.

- PR [#614](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/614): Settings، task و research API در برابر تبدیل ضمنی boolean، مقدار اعشاری و رشتهٔ عددی به integer سخت‌گیرانه شدند؛ تست‌های API عدم mutation در درخواست نامعتبر را بررسی می‌کنند. exact-head CI روی `8e461eaf09952565a56d4e96a11931d091a7002d` موفق شد: tests [#41160661324](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41160661324)، browser E2E [#41160661261](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41160661261)، dependency audit [#41160661265](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41160661265). ادغام: `9feecabd8a82bfa37e2317ee53491a9a07c28568`.
- PR [#615](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/615): همین قرارداد به مدل‌های body در AI-learning، تجربه‌ها، منابع، گراف دانش، candidateها و retention پشتیبان گسترش یافت؛ Literalهای عددی feedback نیز boolean/float را رد می‌کنند. روی head دقیق `6d9b8eed5d4cfa50e98a9a048a406b4fbb8822f8`، tests [#41161075045](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075045) شامل 1719 passed و 5 skipped، Windows storage regressions، artifact/link/import checks و دو benchmark موفق شدند؛ browser E2E [#41161075132](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075132) و dependency audit [#41161075015](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075015) نیز موفق شدند. ادغام: `8ea37617c631c89e6b0a36621061315ee38d5a19`. CI مستقل post-merge روی SHA ادغام‌شده هنوز تأیید نشده است.
- PR [#617](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/617): شمارنده‌های محدود جست‌وجو و دریافت صفحه در مسیرهای بدون منبع به audit پژوهش افزوده شدند؛ فقط نام provider، variant/endpoint مجاز، شمارنده‌ها و دستهٔ خطای allowlist ذخیره می‌شوند و query، URL صفحهٔ ناموفق، متن صفحه یا exception خام ذخیره نمی‌شود. exact-head CI روی `8c6aa6d68bafc95ee2509b59b9db786271e43c70`: tests [#41162151299](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151299) با 1722 passed و 5 skipped، Windows storage regressions، browser E2E [#41162151340](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151340)، dependency audit [#41162151309](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151309)، artifact/link/import checks و benchmarkها موفق شدند. ادغام: `e6c40e2362d1082fb9631009a693db61cfc7fffc`. اجرای مستقل post-merge هنوز تأیید نشده است.
- PR [#629](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/629): آزمون یکپارچگی subprocess ثابت می‌کند دانش منتشرشده پس از شروع Python process جدید و با همان SQLite قابل بازیابی است، draft منتشرنشده وارد retrieval نمی‌شود و query trace توسط process جدید ثبت می‌شود. تست از embedding قطعی fixture استفاده می‌کند و Ollama را فراخوانی نمی‌کند؛ کیفیت پاسخ زنده یا صحت factual را اثبات نمی‌کند. Merge commit: `4e1f89d9c0b7cf332ecf261a4ddc06230b949969`؛ exact-head CI: 1738 passed/5 skipped.
- PR [#631](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/631): استخراج URL از citation cardهای ChatGPT و fallback کنترل Copy response تقویت شد؛ URLهای تاریخی فقط با تطبیق دقیق عنوان در محتوای ذخیره‌شده بازیابی می‌شوند و هیچ URL از روی عنوان حدس زده نمی‌شود. منابع فاقد URL به‌صورت metadata غیرقابل‌کلیک نمایش داده می‌شوند. ادغام: `a6fe76168ab26fc73a7518a014c286e1bff8ff2c`. CI مستقل post-merge: 1740 passed، 5 skipped؛ Windows storage، dashboard smoke، dependency audit، artifact/link/import checks، retrieval benchmark و claim-to-test benchmark (10/10) موفق شدند. [اجرای tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41171335255).
- PR [#634](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/634): هنگام شکست task، درخواست ثبت‌شدهٔ `cancel_requested` به وضعیت terminal `cancelled` و `pause_requested` به `paused` تبدیل می‌شود و هیچ‌کدام با retry خودکار بازنویسی نمی‌شوند. backoff فعلی فقط برای taskهایی که هنوز `running` هستند حفظ شده؛ پیام خطا باقی می‌ماند و lease آزاد می‌شود. تست‌های task-manager و worker شکست شروع heartbeat را پوشش می‌دهند. ADR-013 قرارداد را ثبت کرده است. ادغام: `b50daaaaa12fab13d6526a47335613bd30a63eba`. Post-merge CI: 1746 passed/5 skipped؛ Windows storage، dashboard smoke، dependency audit، artifact/link/import checks و benchmarkها موفق.
- PR [#633](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/633): تشخیص سلامت کامل TaskRuntime از زنده‌بودن حداقل یک thread جدا شد؛ startup فقط runtime سالم را reuse می‌کند و restart پس از توقف و join محدود threadهای باقی‌مانده انجام می‌شود. اگر thread قبلی در مهلت ۱۰ ثانیه متوقف نشود، ایجاد worker جایگزین متوقف می‌شود تا دو worker با شناسهٔ یکسان هم‌زمان اجرا نشوند. تست‌های regression خروج غیرمنتظرهٔ worker و maintenance thread و recovery هنگام startup را پوشش می‌دهند؛ قرارداد در `docs/architecture/ADR-012-task-runtime-health-recovery.md` ثبت شده است. ادغام: `f900dc014c491e2dc6a01cdcf1f22bd230c31bb4`. Post-merge CI روی همان SHA: 1742 passed/5 skipped؛ Windows storage، dashboard smoke، dependency audit، artifact/link/import checks و هر دو benchmark موفق.
- PR [#636](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/636): lane و fallback TaskRuntime پیش از فراخوانی start() در registry ثبت می‌شوند؛ بنابراین اگر startup پس از ایجاد بخشی از threadها شکست بخورد، مسیر shutdown همچنان runtime را می‌بیند و می‌تواند cleanup را تکرار کند. schema، semantics صف، lease و handler تغییر نکرده‌اند. regression با تزریق خطای start، ثبت زودهنگام runtime و توقف آن در shutdown را اثبات می‌کند؛ تصمیم در `docs/architecture/ADR-014-track-runtime-before-start.md` ثبت شده است. ادغام: `1fc3c3193eae01c082e8129292c1d7c6b18fa106`.
- PR [#637](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/637): هر snapshot دیتابیس در staging قبل از انتشار مجدداً با SHA-256، SQLite user_version و فهرست جدول‌های manifest تطبیق داده می‌شود؛ mismatch باعث توقف و پاک‌سازی staging می‌شود. format_version فقط integer واقعی می‌پذیرد و boolean/float رد می‌شوند. regression تغییر فایل معتبر بین preflight و copy و manifest version بولی را پوشش می‌دهد؛ قرارداد و محدودیت manifest به‌عنوان integrity record (نه امضای اصالت) در `docs/architecture/ADR-015-verify-staged-restore.md` ثبت شده است. ادغام: `41fe241ed13ac00ef130edb60d3dc3564f681bdc`.
- PR [#639](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/639): `TaskRuntime.is_healthy` اکنون در صورت ثبت درخواست shutdown مقدار false می‌دهد، حتی اگر handler و threadهای قدیمی هنوز در حال خروج باشند. `start()` در این حالت از مسیر restart محدود استفاده می‌کند و تا توقف threadهای قبلی worker جایگزین نمی‌سازد؛ timeout همچنان fail-closed است. regression توقف timeoutدار و راه‌اندازی مجدد پس از پایان worker قبلی را پوشش می‌دهد؛ تصمیم در `docs/architecture/ADR-016-runtime-restart-after-stop.md` ثبت شده است. ادغام: `39a4d1e78d4cea3803a0e53e07d88933b8abe548`. Post-merge CI: 1750 passed/5 skipped؛ Windows storage، dashboard smoke، dependency audit، artifact/link/import checks و benchmarkها موفق.

- PR [#611](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/611) added an API regression for a previously approved/available cloud candidate whose last observation is 31 days old. It independently exercises stale claim review and candidate review, expects HTTP 409 for both, and verifies the list API exposes stale freshness with availability reset to unknown. The change is test-only; production behavior and schema are unchanged. Exact PR-head CI on `ec693cb218b7f311c7ed82458d67ada0f58cdf4a` passed: tests [#41158610985](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610985) (including Windows storage regressions and repository quality checks), browser E2E [#41158611003](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158611003), and dependency audit [#41158610974](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610974). Squash merge commit: `2fd885a583e0c6252aa6b77a23c5cff56bc87c31`. The available GitHub evidence did not confirm post-merge workflows on the squash SHA, so post-merge CI is not claimed. Automated Codex review could not complete because the repository review quota was exhausted; no completed independent code review is claimed.

- PR [#582](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/582): انتخاب‌گر هزینه/کیفیت مدل به مسیر واقعی `learning.run_job` متصل شد؛ مدل نصب‌شده با پروفایل صریح انتخاب می‌شود و انتخاب در provenance ثبت می‌شود. PR #584 دامنهٔ برآورد را به claim review اختیاری، استفاده از همان مدل و رزرو durable مستقل گسترش داد؛ بنابراین محدودیت قدیمی «عدم احتساب claim review» دیگر معتبر نیست. ادغام PR #582: `7ccba9de397f6de63c2535d2d2e955af245e7da1`.
- PR [#581](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/581): cleanup هم‌زمان کانتینرهای orphan به‌صورت idempotent ایمن شد؛ حذف ناموفق فقط پس از تأیید نبودن container skip می‌شود و خطای باقی‌ماندن/عدم امکان تأیید گزارش می‌شود. ادغام: `483343a2d9d67b6c8f7e5133497c8476440cfa5e`؛ CI پس از ادغام شامل real-Docker Sandbox integration [#41033597779](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41033597779)، tests [#41033597853](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41033597853)، browser E2E [#41033597741](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41033597741) و dependency audit [#41033597837](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41033597837) موفق بود.
- PR [#580](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/580): دفتر پذیرش و شواهد اجرای پروژه با baseline و وضعیت فعلی همگام شد؛ ادغام: `0521424dd4882bbe61d69dc95321ed53238f5e2e`.
- PR [#569](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/569): نوع دکمهٔ refresh در `app/static/index.html` صریحاً `button` شد و تست parser-based در `tests/test_ui_semantics.py` اضافه شد تا تمام دکمه‌های HTML ثابت داشبورد فقط یکی از نوع‌های معتبر `button`، `submit` یا `reset` داشته باشند. اسکن داشبورد 56 دکمه را بررسی کرد. روی head دقیق PR، suite کامل 1459 passed و 5 skipped، Windows storage regressions، browser E2E و dependency audit موفق شدند. PR با merge commit `d5695c4e7459298a2b0bb464c3b5a6a9501b30f8` ادغام شد و CI مستقل post-merge روی همان SHA نیز با هر سه workflow موفق تأیید شد.
- PR [#567](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/567): صف durable اجرای یادگیری AI با FIFO پایدار، بازیابی پس از restart و pause/resume امن تکمیل شد؛ pause حین اجرا run را terminal-cancel نمی‌کند و completion هم‌زمان با pause در تراکنش ذخیره‌سازی مهار می‌شود تا lesson ناخواسته ثبت نشود. دو regression test برای این raceها افزوده شد. PR با merge commit `0640c7f02f8f234b8f9b5075deda47c9fc2d3f2a` ادغام شد؛ CI پس از ادغام روی همین SHA شامل 1458 passed و 5 skipped، Windows storage regressions، browser E2E و dependency audit همگی موفق بود.
- PR [#515](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/515): آزمون fault-injection برای شکست موقت ذخیرهٔ نتیجهٔ وظیفه و جلوگیری از رقابت در monkeypatch سراسری؛ CI روی head دقیق موفق شد.
- PR [#516](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/516): اعمال پیش‌نیازها پیش از تغییر وضعیت سرفصل و جلوگیری از حذف درس موجود هنگام تولید مجددِ مسدودشده؛ CI روی head دقیق موفق شد.
- PR [#517](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/517): بازیابی retryable هنگام شکست ساخت/شروع thread تمدید lease؛ handler بدون heartbeat اجرا نمی‌شود؛ CI روی head دقیق موفق شد.
- PR [#518](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/518): جلوگیری از تکمیل دستی بدون assessment موفق، قفل‌کردن نمره‌های دارای منشأ ارزیابی و اعتبارسنجی نمره؛ CI روی head دقیق موفق شد.
- PR [#519](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/519): migration نسخهٔ 39 برای ثبت و backfill منشأ نمره، و یکپارچه‌سازی assessment عملی sandbox با گیت عمومی تکمیل؛ CI روی head دقیق موفق شد.
- PR [#521](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/521): ثبت assessment عملی با idempotency key مبتنی بر شناسهٔ وظیفه؛ retry همان task تلاش تکراری نمی‌سازد و payload متفاوت با همان کلید رد می‌شود؛ test، audit وابستگی، browser E2E و sandbox integration CI موفق شدند.
- PR [#522](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/522): پس از تشخیص قطع‌شدن context مرورگر، یک relaunch کنترل‌شده مجاز است؛ پس از قطع‌شدن دوباره، سیستم برای جلوگیری از چرخهٔ بازشدن Chrome به بازیابی دستی نیاز دارد؛ test، audit وابستگی و browser E2E موفق شدند.
- PR [#524](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/524): بازیابی startup و انتخاب کار بعدی صف یادگیری مستقیماً از SQLite و بدون سقف صفحه‌بندی داشبورد انجام می‌شود؛ آزمون‌های رگرسیون برای بیش از ۵۰ run در یک دوره و بیش از ۱۰۰ دوره اضافه شدند؛ test، dependency audit، browser E2E و Windows storage regressions موفق شدند.
- PR [#528](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/528): lease پایدار SQLite با token مختص فرایند و heartbeat برای جلوگیری از claim هم‌زمان دورهٔ یادگیری مرورگر اضافه شد؛ تست، dependency audit، browser E2E و Windows storage regressions روی head دقیق موفق شدند.
- PR [#531](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/531): lease پس از اتمام recovery هنگام startup آزاد می‌شود تا فرایند بیکار تا انقضای چهار دقیقه‌ای مالکیت را نگه ندارد؛ تست رگرسیون و ADR-007 (تصمیم lease مرورگر) به‌روزرسانی شدند؛ تمام workflowهای لازم روی head دقیق موفق شدند.
- PR [#526](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/526): مسیر قدیمی‌تر `/api/learning/browser/courses` اکنون هنگام startup دوره‌های قطع‌شده را به‌طور خودکار بازیابی می‌کند؛ درس‌های کامل حفظ می‌شوند، دوره‌ها با dispatcher تک‌نمونه‌ای به ترتیب اجرا می‌شوند و پس از شکست یک دوره، صف برای جلوگیری از شکست زنجیره‌ای متوقف می‌شود. test، dependency audit، browser E2E و Windows storage regressions موفق شدند.
- PR [#534](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/534): recovery فقط دوره‌های واقعاً `running` را به `interrupted` تبدیل می‌کند و دوره‌های از قبل `queued` را دست‌نخورده نگه می‌دارد؛ درس‌های تکمیل‌شده حفظ می‌شوند. تست کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `d07e3d692650479a010ba2e182e5fee276318fc7` موفق شدند. ادغام در `main` با commit `54b56831fe7dcaa719b031d659a3f7e9bf19703d` انجام شد. CI تجمعی روی `main` پس از تغییرات بعدی، از جمله commit `d7c7fc8df0240b4cdea5cdbd0e06b868d6f9bc6d`، موفق است؛ این شواهد را نباید به‌عنوان اجرای جداگانه روی خود merge commit تفسیر کرد.
- PR [#536](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/536): تلاش مجدد محدود و زمان‌بندی‌شده برای دوره‌های یادگیری مرورگر، بازیابی اجرای مانده با رعایت lease و جلوگیری از تغییر وضعیت دورهٔ تکمیل‌شده توسط worker قدیمی اضافه شد. آزمون‌ها، Windows storage regressions، browser E2E و dependency audit روی head دقیق موفق شدند؛ ادغام `e0c86fb6fae4a83e78f07d56eb3f95af0f1861a3` انجام شد و سه workflow پس از ادغام `main` نیز موفق شدند.
- PR [#537](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/537): مسیر گفت‌وگوی عادی نیز زبان پاسخ را بررسی می‌کند، یک اصلاح محدود انجام می‌دهد و در صورت شکست اصلاح یا پاسخ خالی به fallback ثابت برمی‌گردد. آزمون‌ها، Windows storage regressions، browser E2E و dependency audit روی head دقیق موفق شدند؛ ادغام `4499e0d59254c1186eb82a0612909dc96f08179b` انجام شد؛ آزمون‌ها، Windows storage regressions، browser E2E و dependency audit پس از ادغام روی `main` نیز موفق شدند.
- PR [#539](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/539): کنترل‌های مفید/غیرمفید برای پاسخ‌های دستیار به پیام و گفت‌وگوی مشخص متصل شدند؛ migration نسخهٔ 5 بازخورد را جدا از دانش ذخیره می‌کند و حذف پیام/گفتگو را با cascade رعایت می‌کند. API فقط پیام دستیار را می‌پذیرد، امتیاز قابل تغییر است و هیچ ارتقای خودکار دانش رخ نمی‌دهد. آزمون‌های دقیق PR: 1415 موفق، 5 مورد skip؛ Windows storage regressions، browser E2E و dependency audit نیز موفق شدند. ادغام `daeb3dcf57200ac6cd6ca9b0c4e8dedf76123d8f` انجام شد و سه workflow پس از ادغام `main` نیز موفق شدند.
- PR [#541](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/541): اعتبارسنجی امتیاز بازخورد در API به `StrictInt` تغییر کرد تا مقدارهای boolean و رشتهٔ عددی به‌اشتباه پذیرفته نشوند؛ آزمون کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `0d4aa31da97fe1113c533a356e44d469fc6ec575` موفق شدند. ادغام در `main` با commit `7005626755f9616e9bbca95c4a4521a6af64e20c` انجام شد؛ آزمون کامل، browser E2E و dependency audit پس از ادغام روی همین commit نیز موفق شدند.
- PR [#543](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/543): پاسخ خالی یا غیرمتنی مدل در چت عادی و پژوهشی دیگر به‌عنوان پاسخ معتبر عبور نمی‌کند؛ fallback بومی‌سازی‌شده برگردانده می‌شود و مسیر پژوهش وضعیت `generation_failed` را بدون متن خام ثبت می‌کند و بررسی ادعا را اجرا نمی‌کند. آزمون کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `419a3f35838a32d2da97c1aec9a6ce75006bb90c` موفق شدند؛ آزمون کامل 1423 موفق و 5 مورد skip گزارش کرد. ادغام با commit `7005626755f9616e9bbca95c4a4521a6af64e20c` انجام شد؛ آزمون کامل، Windows storage regressions، browser E2E و dependency audit پس از ادغام نیز موفق شدند.
- PR [#544](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/544): خروجی خالی یا غیرمتنی در اصلاح زبان و اصلاح ادعاها پیش از بررسی مجدد یا ترکیب پاسخ رد می‌شود؛ مسیر پژوهش شکست تولید را با دلیل محدود ممیزی می‌کند و خروجی نامعتبر وارد بازبینی ادعا یا ثبت تجربه نمی‌شود. تست‌های رگرسیون رشتهٔ خالی، whitespace، `None` و شیء غیرمنتظره را پوشش می‌دهند. تست کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `a62b3a7b5592aa0bbe8275609ba62f93e2b670c5` موفق شدند؛ ادغام با commit `26020e3e036040d762ef50ffa8dd224f12c52a90` انجام شد و تمام workflowهای پس از ادغام نیز موفق شدند.
- Commit `f7b07db82b56f273993db31a142cff06939402ff`: در صورت خطای تراکنش بازیابی startup، آزادسازی lease در مسیر `finally` تضمین و آزمون رگرسیون اضافه شد؛ آزمون کامل [#40915388420](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915388420)، browser E2E [#40915388433](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915388433) و dependency audit [#40915388571](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915388571) موفق شدند.
- Commit `b0e830cf2e23c5971a9509cfd70314d8fc2c5d1a`: مستندات مرز صف تک‌نمونه‌ای `app/ai_learning` و lease مستقل زیرسامانهٔ قدیمی مرورگر را روشن کردند؛ آزمون کامل [#40915616538](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915616538)، browser E2E [#40915616417](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915616417) و dependency audit [#40915616647](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40915616647) موفق شدند.
- PR [#565](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/565): آزمون مرورگر برای شکست API ساخت پشتیبان با HTTP 503 اضافه شد؛ پیام خطای فارسی، فعال‌شدن دوبارهٔ کنترل و نبود خطای uncaught مرورگر بررسی می‌شود. آزمون درخواست را در لایهٔ مرورگر intercept می‌کند و به storage واقعی دست نمی‌زند؛ کد اجرایی برنامه تغییر نکرد. تمام چهار بررسی الزامی روی head دقیق PR موفق شدند و CI پس از ادغام روی `main` نیز با موفقیت تکمیل شد.
- Commit `d7c7fc8df0240b4cdea5cdbd0e06b868d6f9bc6d`: آزمون واقعی مرورگر برای بازخورد مفید/غیرمفید جست‌وجوی AI-learning اضافه شد؛ دادهٔ دانشی فقط در دیتابیس موقت CI seed می‌شود، شناسهٔ trace به payload بازخورد متصل و شمارنده‌های ذخیره‌شده بررسی می‌شوند. هر سه workflow روی همین SHA موفق شدند.
- این شواهد، آزمون خودکار مخزن هستند؛ جایگزین ارزیابی زنده، پذیرش میزبان هدف، تمرین restore/rollback یا بازبینی امنیتی مستقل نیستند.

- PR [#587](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/587): تمدید lease وظیفه فقط خطاهای موقت SQLite از نوع `BUSY`/`LOCKED` را تا deadline مبتنی بر ساعت monotonic دوباره امتحان می‌کند؛ خطاهای دائمی مانند disk I/O و نبود جدول فوراً heartbeat را متوقف می‌کنند. تست‌های رگرسیون و ADR-003 به‌روزرسانی شدند؛ ادغام `eeab2cbc8e6daecde1a9407b9e5025643f8db59e` و CI روی head دقیق موفق شد.
- PR [#588](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/588): DNS rebinding در درخواست‌های بازبین خارجی و بررسی منبع یادگیری مهار شد؛ IP عمومی اعتبارسنجی‌شده به اتصال TCP pin می‌شود، hostname برای TLS حفظ می‌شود، proxy و redirect غیرفعال‌اند، پورت‌ها محدود و پاسخ بازبین به 64 KiB محدود است. `httpx` از وابستگی runtime حذف و در وابستگی‌های توسعه نگه داشته شد؛ تست‌های مرزی و سند امنیتی افزوده شدند. ادغام `e08b580670999926a5350e3d00bcb48b208492eb`؛ CI head دقیق شامل tests، Windows storage، browser E2E و dependency audit همگی موفق شدند. پس از ادغام، CI روی `main` در `019e5589...` با 1537 passed و 5 skipped و سه workflow اصلی موفق شد.

- PR [#589](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/589): مرز درخواست‌های GitHub برای self-improvement سخت‌گیرانه شد؛ proxyهای محیطی غیرفعال و redirectها رد می‌شوند تا bearer token از مبدأ ثابت `api.github.com` خارج نشود. تست بدون شبکه و سند مرز امنیتی افزوده شد؛ ادغام `b3040e5122cad109a608403d331e3ad8e3312285`؛ tests/Windows، browser E2E، dependency audit و Sandbox integration روی head دقیق موفق شدند.
- PR [#590](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/590): نصب‌کنندهٔ Ollama اکنون نسخه و دارایی رسمی GitHub را اعتبارسنجی می‌کند، اندازه و SHA-256 را قبل از اجرا بررسی می‌کند، cache را دوباره hash می‌کند و فایل معتبر قبلی را در دانلود نامعتبر حفظ می‌کند. محدودیت باقی‌ماندهٔ Linux second-stage payload در `docs/security/OLLAMA_INSTALLER_INTEGRITY.md` صریحاً ثبت شده است؛ ادغام `3cefdf5928eeae6183f3cb317381ec9b1b43ab6a`؛ tests، browser E2E و dependency audit روی head دقیق موفق شدند.
- PR [#591](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/591): دو جریان واقعی Chromium برای رضایت، refresh دستی، revoke و بازیابی خطای Kaggle/OCI اضافه شد؛ مسیر OCI صریحاً عدم مجوز اجرای remote را نشان می‌دهد. تست‌ها APIها را intercept می‌کنند و CLI یا credential واقعی را اجرا نمی‌کنند؛ ادغام `caf3f49a0ce5f411351195e412d7a3cd6a56e4a9`؛ tests/Windows، browser E2E و dependency audit موفق شدند.
- PR [#592](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/592): تغییر هویت executable در Kaggle یا scope تنظیمات OCI، snapshotهای قدیمی را در همان تراکنش پاک می‌کند؛ ذخیرهٔ نتیجهٔ refresh در حال اجرا نیز consent generation و scope را دوباره بررسی می‌کند تا دادهٔ حساب قبلی نمایش داده نشود. ADR-010 و تست‌های API برای تغییر scope و race افزوده شدند؛ ادغام `019e5589da3d9f13a6fe47f177ee5a8d8defb213`؛ tests/Windows، browser E2E و dependency audit موفق شدند.
- PR [#593](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/593): محدودیت طول textarea برنامه‌نویسی با `programming_source_max_chars` ذخیره‌شده همگام شد و پس از ذخیرهٔ موفق تغییر می‌کند؛ تست Chromium ارسال، polling، نمایش نتیجه، خطای 503 و عدم اجرای خودکار کد/آزمون را بررسی می‌کند. CI روی head دقیق شامل tests با 1537 passed و 5 skipped، Windows storage regressions، browser E2E و dependency audit موفق شد؛ ادغام `c5e8a7c338c35b0c9267bb563344b9973ac56153`. CI پس از ادغام روی همین SHA نیز کامل موفق شد: tests [#41065147895](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147895) با 1537 passed و 5 skipped، Windows storage regressions، browser E2E [#41065147794](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147794) و dependency audit [#41065147868](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147868).

- PR [#595](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/595): مسیرهای دریافت صفحه در knowledge base و public web sources به سیاست URL بدون DNS و resolver واحد در transport منتقل شدند؛ تمام پاسخ‌های DNS بررسی و IP عمومی انتخاب‌شده به اتصال pin می‌شود، Proxy محیطی صریحاً غیرفعال است، و redirect قبل از اتصال دوباره resolve نمی‌شود. محدودیت طول URL دانش و تست‌های regression برای proxy isolation و یک‌بار resolve افزوده شدند. ادغام `dbfb8849ca4b2e243abeded724635ab47eb7d364`؛ CI روی head دقیق `616b5693863e1559bc51efbfa4200a5690ed55cf` شامل 1543 passed و 5 skipped، Windows storage، browser E2E و dependency audit موفق شد. CI پس از ادغام روی `main` هنوز در حال اجراست.

## 2. قابلیت‌های پیاده‌سازی‌شده

### 2.1 یادگیری و دانش

- [x] مسیر پژوهش‌محور موضوع‌به‌سرفصل‌به‌درس با `gpt-oss:20b` در PR [#709](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/709) پیاده‌سازی شده است؛ آخرین exact-head CI روی `bc5de1b891526930f1b4b766505843e03c499410` سبز شد (۱۹۷۱ تست موفق، ۵۰ مورد deselected؛ Windows storage، browser E2E و dependency audit موفق).
- [ ] آزمون واقعی بارگذاری/استنتاج و سنجش RAM/latency/کیفیت روی میزبان هدف؛ تا تکمیل این گیت قابلیت merge نمی‌شود.

شرح جامع رفتار فعلی، محدودیت‌ها، سطوح شواهد، ارزیابی و نقشهٔ راه یادگیری در [راهنمای جامع زیرسامانه یادگیری](LEARNING.md) نگهداری می‌شود تا این سند به مرجع تکراری تبدیل نشود.

### 2.2 اجرای وظایف، رابط و کنترل تغییرات

- [x] صف پایدار SQLite با lease، checkpoint، retry محدود، لغو، توقف/ادامه و بازیابی وظایف.
- [x] اجرای handlerهای allow-list شده و رد نوع وظیفهٔ ناشناخته به‌جای اجرای payload دلخواه.
- [x] sandbox مبتنی بر Docker و گردش‌کار کنترل‌شدهٔ آزمون تغییرات.
- [x] گردش‌کار پیشنهاد تغییر با allowlist، آزمون ایزوله، تأیید صریح و ایجاد draft PR/rollback؛ ادغام یا استقرار خودکار فعال نیست.
- [x] رابط محلی برای چت، مدیریت وظایف، یادگیری، بررسی تجربه، سلامت ذخیره‌سازی و پشتیبان‌گیری/بازیابی مرحله‌ای.
- [x] تشخیص سخت‌افزار و محدودسازی اولیهٔ منابع برای اجرای local-first با Ollama.
- [x] بررسی CI و قراردادهای خودکار تولید پرامپت‌های بازبینی از فایل منبع.
- [x] دفتر idempotent ثبت تلاش‌های بازبینی نامزد یادگیری با migration نسخهٔ 24؛ نتیجهٔ کانال به‌تنهایی اعتماد یا دانش را ارتقا نمی‌دهد.
- [x] ثبت شواهد وب و مشاورهٔ peer، رزرو اجرای قابل تکرار، و سیاست‌های بودجهٔ پایدار برای pipeline یادگیری (migrations 28–31).
- [x] اتصال منابع وب عمومی به IP اعتبارسنجی‌شده با حفظ بررسی نام میزبان TLS و اعتبارسنجی مجدد redirectها برای کاهش خطر DNS rebinding.
- [x] گردش‌کار یادگیری AI-to-AI از طریق تب Chrome محلی و احراز‌شده، با سقف تعداد درس، ذخیرهٔ شواهد و بدون ارتقای خودکار اعتماد.
- [x] بازبینی factual با کانال‌های مستقل، oracleهای نسخه‌دار، ثبت هش/شواهد و مدیریت چرخهٔ عمر دانش؛ رأی مدل به‌تنهایی حقیقت را اثبات نمی‌کند.
- [x] نقشهٔ قابلیت‌های یادگیری با مصرف توکن/زمان استنتاج، هزینهٔ تخمینی و شواهد عملی/انتقالی با برچسب موقت.
- [x] گیت ساختاری پوشش ادعا به آزمون و benchmark نسخه‌دار در CI؛ کفایت معنایی آزمون همچنان نیازمند بازبینی جداگانه است.

## 3. دروازه‌های باقی‌ماندهٔ کل پروژه

- [ ] تکمیل ممیزی کل مخزن، مرزهای مالکیت داده و وابستگی‌های زمان اجرا؛ بررسی فایل‌های GitHub به‌تنهایی ممیزی کامل checkout نیست.
- [ ] اجرای ابزار baseline روی میزبان هدف و نگهداری گزارش redacted شامل commit/runtime و سلامت ذخیره‌سازی.
- [ ] اجرای benchmark صف و آزمون‌های بار/بازیابی روی سخت‌افزار هدف و آرشیو نتایج.
- [ ] اعتبارسنجی مدل `gpt-oss:20b` و صف درس‌های پژوهش‌محور روی Windows با RAM 32GB و Core i5-12400؛ ثبت زمان بارگذاری، peak RAM، زمان هر درس و کیفیت فارسی/انگلیسی.
- [ ] تکمیل fault injection و سناریوهای توقف/لغو/بازیابی برای همهٔ انواع وظیفه.
- [ ] تمرین عملی backup، restore روی staging و update/rollback طبق runbook.
- [ ] تکمیل بازبینی امنیتی مستقل برای [Issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) و ثبت نتیجهٔ اصلاحات.
- [x] دروازهٔ CI برای نبود چرخه‌های import زمان بارگذاری ماژول؛ این آزمون جایگزین بازبینی وابستگی‌های پویا یا اثبات جداسازی کامل معماری نیست.
- [ ] بازبینی گسترده‌تر مرزهای ماژول و وابستگی‌های پویا؛ اسکن فعلی import cycle به‌تنهایی اثبات جداسازی معماری نیست.
- [x] ممیزی PRهای قدیمی و حذف هم‌پوشانی از صف ادغام: قابلیت‌های معتبر #462/#495 و #370 به‌صورت هدفمند در #497 و #499 منتقل شدند؛ شاخه‌های قدیمی #459، #462، #495، #369 و #370 بسته شدند تا از ادغام نسخه‌های stale جلوگیری شود.
- [ ] تکمیل پذیرش عملیاتی و امنیتی رابط/ذخیره‌سازی روی میزبان مقصد.

## 4. قابلیت‌هایی که عمداً فعال نیستند

این موارد تا زمانی که مجوز و دروازهٔ مستقل تعریف و تأیید نشود، «کار ناقصِ لازم برای تکمیل عادی» محسوب نمی‌شوند:

- provisioning منابع ابری و اجرای remote غیرفعال‌اند تا مجوز ارائه‌دهنده، سقف هزینه، ممیزی و پذیرش امنیتی تعیین شوند.
- ادغام خودکار PR، استقرار خودکار و تغییر مستقیم محیط production عمداً غیرفعال‌اند؛ گردش‌کار فعلی پیشنهاد و تأیید کنترل‌شده است.
- ارتقای خودکار سطح اعتماد factual بر اساس رأی مدل غیرفعال است.

## 5. محدودیت‌های مهم تفسیر وضعیت

- تیک به معنی وجود شواهد پیاده‌سازی در مخزن است، نه تضمین صحت علمی یا آمادگی production.
- نمرهٔ فعلی آزمون از تطبیق متنی نرمال‌شده می‌آید؛ پاسخ درست با عبارت‌بندی متفاوت ممکن است نمره نگیرد و پاسخ منطبق لزوماً فهم را ثابت نمی‌کند.
- بازبینی معنایی assessment اختیاری و advisory است و به مدل نصب‌شده‌ای با digest متمایز از مدل‌های تولید نیاز دارد؛ با این حال، این سازوکار به‌تنهایی تأیید مستقل حقیقت factual یا پذیرش علمی محسوب نمی‌شود.
- مرور فاصله‌دار و پیشنهادها پیاده‌سازی شده‌اند، اما به‌تنهایی تسلط را ثابت نمی‌کنند و کل چرخهٔ یادگیری را خودکار نمی‌کنند.
- تست‌های محلی و تست‌های انسانی عمداً در فهرست کارهای باقی‌مانده بالا تکرار نشده‌اند؛ این سند دربارهٔ کارهای پیاده‌سازی و دروازه‌های غیر از آن دو دسته است.

## 6. منابع پیاده‌سازی مرتبط

- `app/learning.py`, `app/learning_recommendations.py`, `app/spaced_repetition.py`
- `app/factual_knowledge.py`, `app/factual_verification_routes.py`, `app/factual_verification_tasks.py`
- `app/chat_research.py`, `app/experience.py`, `app/experience_retrieval.py`
- `app/task_manager.py`, `app/task_runtime.py`, `app/task_worker.py`
- `docs/LEARNING.md` (مرجع واحد زیرسامانهٔ یادگیری)
- `docs/SYSTEM_REQUIREMENTS.md` (نیازمندی‌های هدف؛ نگه داشته می‌شود چون مشخصات طراحی است)


## 2026-10-30 — تکمیل ممیزی و شواهد CI

- PR [#576](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/576): آزمون Chromium برای بازیابی Cloud Planner پس از پاسخ API با HTTP 503 اضافه شد؛ فعال‌شدن مجدد کنترل و نبود خطای uncaught بررسی شد. ادغام: `783e03656eba8e94886c3000c2eb0a00cc3f52eb`؛ CI پس از ادغام: tests [#41021548810](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41021548810)، browser E2E [#41021548808](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41021548808)، dependency audit [#41021548850](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41021548850) — موفق.
- PR [#575](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/575): مستندات پوشش Sandbox بازپایه‌گذاری و ادغام شد. commit: `4dd52718b59fa553549d4073e77305a5b1c221a2`؛ CI پس از ادغام: tests [#41022226019](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41022226019)، browser E2E [#41022225912](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41022225912)، dependency audit [#41022226164](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41022226164) — موفق.
- PR [#578](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/578): ادعای stale دربارهٔ آینده‌بودن API برنامه‌ریزی Cloud و ارسال Sandbox با وضعیت واقعی کد هم‌تراز شد. commit: `90bb4ba02ce26bc04d2e3171fb865d4122b74445`؛ CI پس از ادغام tests [#41028946078](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41028946078)، browser E2E [#41028945985](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41028945985)، dependency audit [#41028945972](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41028945972) — موفق.
- PR [#577](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/577): اعتبارسنجی URL loopback در API تنظیمات با سیاست runtime Ollama یکسان شد؛ malformed IPv6، پورت نامعتبر/خالی، credentials و عدم ذخیرهٔ جزئی پوشش داده شدند. commit: `c79e2a008cb722930d2bfe979ceaadc18637318c`؛ CI پس از ادغام tests [#41029619812](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41029619812)، browser E2E [#41029619817](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41029619817)، dependency audit [#41029619653](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41029619653) — موفق.
- PR [#579](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/579): سه default جاافتادهٔ تنظیمات هزینه/کیفیت یادگیری اضافه و API برای مقادیر نوع‌دار و محدوده‌دار ایمن شد؛ آزمون API و Chromium برای ذخیرهٔ موفق و شکست 503 اضافه شد. commit: `cc1ad50536e39b44027c61c903873d8638da1df5`؛ آزمون کامل 1488 passed و 5 skipped، benchmarkهای بازیابی و پوشش ادعا، Windows storage، browser E2E و dependency audit همگی موفق شدند.
- PR قدیمی [#572](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/572) به‌دلیل انحراف شاخه بسته شد؛ اصلاحات معتبر معماری در PR #578 حفظ و ادغام شدند. هیچ تغییر معتبر منحصربه‌فردی حذف نشد.
- شواهد فوق CI مخزن هستند؛ آزمون زندهٔ Ollama، پذیرش میزبان هدف، تمرین staging/rollback و بازبینی امنیتی مستقل را جایگزین نمی‌کنند.

- PR [#584](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/584): هزینه و توکن claim review اختیاری در برآورد مدل لحاظ شد؛ همان مدل انتخاب‌شده به مرحلهٔ review منتقل و فراخوانی به‌عنوان مرحلهٔ idempotent در بودجهٔ durable رزرو می‌شود. ادغام در `main`: `20e30eeb77bf04e2169ced9db32ba1cff036c3d6`؛ post-merge tests، Windows storage regressions، browser E2E و dependency audit موفق شدند.
- PR [#585](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/585): heartbeat صف وظایف پس از خطای موقت SQLite تا پایان آخرین lease معتبر retry می‌کند؛ deadline با ساعت monotonic کنترل می‌شود و ownership loss، pause/cancel یا خطای غیرمنتظره همچنان fail-closed است. دو تست رگرسیون برای recovery و توقف در deadline اضافه شد و ADR-003-managed-task-runtime به‌روزرسانی شد. ادغام: `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94`. CI روی head دقیق و پس از ادغام (tests، Windows storage، browser E2E و dependency audit) موفق است.


## 2026-10-30 — شواهد رابط منابع دانش و کنترل سراسری AI-learning

- مبنای جاری این گزارش پس از ادغام PR #597، شاخهٔ `main` در commit `54c8f55dd9e2f5e163c454dedf37cdd766429874` است. این SHA از GitHub تأیید شد؛ در زمان ثبت این یادداشت، status check پس از ادغام هنوز در دسترس این ممیزی نبود و نباید موفق فرض شود.
- PR [#596](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/596) با merge commit `e057abf17766368fc21f70020d9b87760dec298a` رابط ثبت منبع HTTPS، فهرست منابع و جست‌وجوی محلی دانش را تکمیل کرد. تست Chromium مسیر ingest/list/search و بازیابی کنترل پس از خطاهای 503 را پوشش می‌دهد. سه workflow روی head دقیق PR موفق شدند: tests، browser E2E و dependency audit. درخواست‌های مرورگر در تست intercept می‌شوند؛ دریافت واقعی سایت خارجی با این E2E اثبات نشده است.
- PR [#597](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/597) با merge commit `54c8f55dd9e2f5e163c454dedf37cdd766429874` آزمون Chromium برای مکث/ادامهٔ سراسری AI-learning و بازیابی پس از خطای موقت resume اضافه کرد. روی head دقیق `feab5df18c7852910da8397155dcd8b7182d717d`، workflowهای tests (شامل Windows storage regressions)، browser E2E و dependency audit همگی موفق شدند.
- دامنهٔ PR #597 فقط کنترل سراسری را با payload صریح `course_id: null` می‌آزماید؛ انتخاب دوره و ارسال شناسهٔ یک دورهٔ مشخص در این آزمون پوشش داده نشده است و نباید به‌عنوان تست آن رفتار گزارش شود.
- هیچ تغییر runtime در PR #597 انجام نشد. هیچ‌یک از این دو PR به‌تنهایی اثبات‌کنندهٔ اجرای زندهٔ ChatGPT/Ollama، بازیابی واقعی مرورگر پس از crash، پذیرش انسانی یا کیفیت factual نیستند.


## 2026-10-30 — پوشش resume دورهٔ انتخاب‌شده و وضعیت main

- PR [#599](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/599) آزمون Chromium مستقلی برای resume یک دورهٔ انتخاب‌شده اضافه کرد؛ fixture یک دوره با شناسهٔ 42 را بارگذاری می‌کند و بررسی می‌کند درخواست resume دقیقاً با `course_id: 42` ارسال شود. تست قبلی pause/resume سراسری بدون تغییر باقی ماند.
- PR #599 در commit `cd620a624e997fc52fd510c0fdfedc2280af02b9` در `main` ادغام شد. روی head دقیق PR یعنی `23832ee9c2cac7ea78412d58f27f2cae5ac40b5d`، tests (شامل Windows storage regressions)، browser E2E و dependency audit همگی موفق شدند: tests run [#41076114713](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076114713)، browser E2E [#41076115543](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076115543)، dependency audit [#41076114829](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076114829).
- این تغییر فقط تست است و هیچ رفتار runtime را تغییر نمی‌دهد. E2E از APIهای intercept‌شده استفاده می‌کند؛ تعامل با provider زنده یا اجرای target-host را اثبات نمی‌کند.
- مبنای فعلی `main` پس از این ادغام `cd620a624e997fc52fd510c0fdfedc2280af02b9` است. CI دقیق head PR موفق است؛ اجرای مستقل post-merge روی همین SHA در شواهد قابل‌دسترسی این بازبینی تأیید نشده و موفق فرض نمی‌شود.


## 2026-10-30 — بازیابی زمان‌بندی‌شدهٔ خطاهای موقت AI-learning (PR #602)

- PR [#602](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/602) با commit ادغام `e02b5941345512353189c25678832f129324f7ff` در `main` ادغام شد. بازیابی خطاهای موقت شبکه، timeout پاسخ، بسته‌شدن قابل‌بازیابی صفحه، rate limit و خطاهای Playwright محدود به حداکثر دو تلاش زمان‌بندی‌شده برای هر اجرا است؛ پاسخ ناقص حفظ می‌شود و cooldown سراسری مانع ارسال موضوعات دیگر به provider در زمان اختلال می‌شود.
- خطاهای نیازمند دخالت کاربر، مانند ورود، تأیید انسانی، نبود وابستگی مرورگر و شکست کیفیت محتوا، وارد retry خودکار نمی‌شوند. صف همچنان تک‌پردازه و worker مرورگر درون‌پردازه است؛ lease توزیع‌شده و بازیابی دقیق state تب ChatGPT اضافه نشده است.
- CI روی head دقیق PR یعنی `9b629f2a5bef699261fbc0097f411e011b656366`: tests [#41087746251](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746251) شامل Windows storage regressions، browser E2E [#41087746167](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746167) و dependency audit [#41087746175](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746175) همگی موفق شدند. مجموعه تست: 1556 passed و 5 skipped؛ بررسی لینک‌های Markdown برای 73 فایل، بررسی import-cycle روی 112 ماژول، تولید artifactها و benchmarkهای CI نیز موفق شدند.
- benchmark واژگانی روی 16 query از fixture: recall@5 برابر 1.0، precision@5 برابر 0.225 و MRR برابر 1.0. benchmark پوشش claim-to-test روی 10 مورد ساختاری موفق شد؛ این benchmarkها کیفیت عمومی retrieval یا کفایت معنایی تست‌ها را اثبات نمی‌کنند.
- commit ادغام‌شده `e02b5941345512353189c25678832f129324f7ff` در گزارش workflowهای PR-triggered قابل مشاهده نبود؛ بنابراین CI مستقل post-merge روی SHA ادغام‌شده تأیید نشده و موفق فرض نمی‌شود. آزمون live provider، پذیرش target-host، crash/multi-process، staging restore/rollback و بازبینی امنیتی مستقل همچنان باز هستند.

## 2026-10-31 — یکپارچگی بستهٔ دومرحله‌ای نصب Ollama در Linux (PR #620)

- مسیر نصب Linux در PR [#620](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/620) اصلاح شد: علاوه بر install.sh، آرشیو واقعی Linux نیز از manifest همان انتشار رسمی GitHub با URL، اندازه و SHA-256 پین‌شده دریافت و پیش از استخراج تأیید می‌شود.
- تابع دانلود ثانویهٔ اسکریپت رسمی با helper داخلی مبتنی بر _download_once جایگزین می‌شود؛ محدودیت اندازه، کنترل redirect و اعتبارسنجی هش از مسیر دانلود موجود پروژه استفاده می‌شوند. ساختار ناشناختهٔ اسکریپت یا معماری پشتیبانی‌نشده باعث توقف امن نصب می‌شود.
- برای سازگاری با shebang رسمی /bin/sh، آرشیو ابتدا به فایل tar موقت decompression می‌شود و سپس استخراج می‌گردد؛ از قابلیت غیر POSIX مانند pipefail استفاده نمی‌شود.
- تست‌های regression انتخاب asset رسمی، digest/size، URL غیرمجاز، نام asset نامعتبر، ناسازگاری نسخه، تغییر ساختار upstream و parse شدن اسکریپت patch‌شده با sh -n را پوشش می‌دهند.
- PR #620 با squash commit `a85c7e85b93c322d6a093ee7ba0319b30a6f2147` در `main` ادغام شد. CI روی head دقیق PR یعنی `5c1c1f36f5f38b43a2ec20e720548fde299a43f8` موفق شد: tests [#41164300720](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300720) با 1728 passed و 5 skipped، Windows storage regressions، artifact validation، لینک‌های Markdown برای 77 فایل، import-cycle scan و benchmarkهای retrieval و claim-to-test (10/10)؛ browser E2E [#41164300693](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300693) و dependency audit [#41164300658](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300658) نیز موفق شدند. Post-merge CI روی squash SHA `a85c7e85b93c322d6a093ee7ba0319b30a6f2147` نیز موفق شد: tests [#41164581056](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164581056) با 1728 passed/5 skipped، Windows storage، dashboard smoke [#41164581077](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164581077) و dependency audit [#41164581067](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164581067) همگی pass شدند. نصب زندهٔ Ollama عمداً اجرا نشده؛ پذیرش روی میزبان Linux همچنان gate جداگانه است.

## 2026-10-31 — یکسان‌سازی سیاست HTTPS در بازیابی منابع پژوهش (PR #622)

- در PR [#622](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/622)، نتایج HTTP پیش از اعمال سقف تعداد نتایج کنار گذاشته می‌شوند؛ در نتیجه نتیجهٔ ناامن/ناسازگار HTTP نمی‌تواند جای منبع HTTPS قابل‌استفاده را در فهرست محدود بگیرد.
- لایهٔ دریافت سند نیز مستقل از مسیر جست‌وجو، URL غیر HTTPS را پیش از هر درخواست شبکه رد می‌کند؛ redirect از HTTPS به HTTP نیز برای جلوگیری از downgrade رد می‌شود. این رفتار با قرارداد citation، payload پژوهش و ثبت شواهد که HTTPS می‌خواهند یکسان است.
- تست‌های regression انتخاب HTTPS پس از نتیجهٔ HTTP، رد HTTP پیش از transport و ثبت شمارنده‌های discovery را پوشش می‌دهند.
- PR #622 با squash commit `c46e136bb00e4dd56627e98990b80d27f73b93e9` در `main` ادغام شد. Exact-head CI و post-merge CI موفق شدند: 1735 passed/5 skipped، Windows storage، dashboard smoke و dependency audit همگی pass شدند.


## 2026-10-31 — امنیت منابع، ممیزی هوش مصنوعی و مستندات

- PR #622: سیاست HTTPS برای کشف و دریافت منابع پژوهش یکسان شد؛ URL غیر HTTPS پیش از شبکه رد می‌شود و redirect از HTTPS به HTTP مجاز نیست. ادغام: `c46e136bb00e4dd56627e98990b80d27f73b93e9`. CI پس از ادغام: 1735 passed/5 skipped؛ Windows storage، dashboard smoke و dependency audit موفق.
- PR #623: provenance نسخهٔ claim review، hash پرامپت، مدل و شمارنده‌های مجاز provider به audit پایدار و privacy-preserving اضافه شد؛ متن خام prompt/answer/claim/reason و source excerpt ذخیره نمی‌شود. ادغام: `1e46eff5aeafd1a470a421df6dbea81629a89266`; post-merge CI کامل موفق.
- PR #624: وضعیت مستندات صف AI-learning و claim-review با پیاده‌سازی موجود همگام شد؛ lease بین‌فرایندی SQLite، fencing، محدودیت تک‌میزبان و claim-review-v2 ثبت شد. ادغام: `acb821df03c8c24c7b18cc912f3a1881964fb863`; exact-head و post-merge CI موفق.
- PR #625: race در آزمون مرورگر پیشنهاد بهبود با انتظار برای پایان refresh فهرست اصلاح شد؛ رفتار runtime تغییر نکرد. ادغام: `04d325e244fc5b231d74ac7b700b571b4cf45283`; exact-head و post-merge CI موفق.
- PR #626: middleware برنامه علاوه بر peer loopback، Host را به `localhost`، `127.0.0.1` و `::1` محدود می‌کند تا DNS-rebinding با Host مهاجم رد شود. این کنترل authentication یا دفاع کامل CSRF نیست. ادغام: `6fdd176e81aa408ff17cc1c01ba66ec198f42240`; post-merge tests 1737 passed/5 skipped و تمام workflowهای اصلی موفق.
- PR #627: مستند امنیت نصب Ollama با پیاده‌سازی واقعی بررسی digest/size payload مرحله دوم Linux همگام شد؛ نصب زنده روی میزبان مقصد همچنان تأیید نشده است. ادغام: `7a2cf05a7b3485e3b157e41860783fda2e76a846`; exact-head CI و post-merge CI روی `main` موفق‌اند: 1737 passed/5 skipped در 176.88 ثانیه، Windows storage، dashboard smoke و dependency audit نیز موفق.
- آخرین شمارش درخت Git: 400 فایل، 285 فایل Python، 157 فایل تست Python، 71 سند Markdown زیر docs و 41 تابع مستقل Browser E2E. این شمارش جایگزین بازبینی خط‌به‌خط همهٔ فایل‌ها نیست.
- دروازه‌های باز: اجرای زنده و human adjudication، بازبینی امنیتی مستقل Issue #52، پذیرش crash/soak روی میزبان هدف، staging restore/rollback، و آزمون واقعی Docker/target-host.

## 2026-10-31 — TaskRuntime partial-thread recovery (PR #633)

- `TaskRuntime.is_running` remains an activity signal for shutdown; new `is_healthy` requires every configured worker and optional maintenance thread to be alive.
- `start()` is idempotent only for a healthy runtime. A partial runtime is stopped and joined before replacements are created; if any prior thread does not stop within the bounded 10-second deadline, restart raises `RuntimeError` and does not overlap worker identities.
- Application startup now reconstructs an unhealthy runtime set. Regression tests cover worker exit, maintenance exit, and partially alive startup recovery. The contract and single-process limitations are recorded in [ADR-012](architecture/ADR-012-task-runtime-health-recovery.md).
- PR [#633](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/633) merged as `f900dc014c491e2dc6a01cdcf1f22bd230c31bb4`. Post-merge CI: 1742 passed/5 skipped in 138.58s; Windows storage, dashboard smoke, dependency audit, artifact/link/import checks, and both deterministic benchmarks passed.

## 2026-10-31 — تقدم درخواست pause/cancel بر retry صف وظایف (PR #634)

- `fail_task()` اکنون وضعیت persisted task را داخل همان تراکنش `BEGIN IMMEDIATE` بررسی می‌کند. اگر کاربر پیش از شکست، pause یا cancel را ثبت کرده باشد، این درخواست بر retry خودکار اولویت دارد.
- `cancel_requested` به `cancelled` و `pause_requested` به `paused` تبدیل می‌شود؛ backoff محدود قبلی فقط برای taskهای همچنان `running` اعمال می‌شود. خطای تشخیصی حفظ و lease در هر حالت آزاد می‌شود.
- تست‌های task-manager و worker شکست راه‌اندازی heartbeat را پوشش می‌دهند؛ handler در این مسیر اجرا نمی‌شود، task لغوشده دوباره claim نمی‌شود و task متوقف‌شده فقط با resume صریح فعال می‌شود. تصمیم در [ADR-013](architecture/ADR-013-task-control-priority.md) ثبت شده است.
- PR [#634](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/634) با commit `b50daaaaa12fab13d6526a47335613bd30a63eba` ادغام شد. Post-merge CI: 1746 passed/5 skipped در 211.87s؛ Windows storage، dashboard smoke، dependency audit، artifact/link/import checks و benchmarkها موفق.

- PR [#669](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/669): evaluator اکنون می‌تواند زبان مورد انتظار فارسی/انگلیسی را به‌صورت تشخیصی بررسی کند؛ code و URL از شمارش script کنار گذاشته می‌شوند، شواهد ناکافی fail-closed است و آستانهٔ ۶۵٪ با طبقه‌بندی script غالب هم‌راستا است. regressionهای متقارن ۶۰٪ رد و ۶۵٪ قبول اضافه شد. ادغام: `47ddaa7973c59115e6e41aed7ebf70ebd3778bd6`; exact-head CI: 1807 passed/47 deselected، Windows storage، dashboard smoke، browser E2E، dependency audit، 90 Markdown files و benchmark 10/10. این تشخیص جایگزین ارزیابی معنایی یا داوری انسانی نیست.


## 2026-11-01 — وضعیت پس از PR #671

- PR #671 برای جلوگیری از ذخیرهٔ پیام خام exception و diagnostics غیرقابل‌اعتماد در کشف منابع ابری ادغام شد: `2a5c83bf0ba924f4a2c8f206338a04872562d72d`.
- CI دقیق PR موفق است. Post-merge dependency audit و Windows storage موفق شده‌اند؛ تست اصلی و dashboard smoke در زمان این checkpoint هنوز در حال اجرا هستند. موفقیت نهایی آن‌ها تا تکمیل workflow ادعا نمی‌شود.
- بررسی ایستای مسیر research نشان داد payload حداکثر سه منبع HTTPS می‌گیرد و audit پژوهش diagnostics را با allowlist جداگانه ذخیره می‌کند. علت گزارش قبلیِ صفر منبع در اجرای زنده هنوز اثبات نشده؛ تغییرات حدسی در discovery انجام نشد.
- Issues #290 و #52 باز هستند. بررسی امنیتی مستقل Ollama، اجرای واقعی روی میزبان هدف، staging/rollback، ارزیابی زنده و داوری انسانی همچنان لازم‌اند.


### تکمیل CI پس از ادغام PR #671

- سه workflow پس از ادغام روی SHA دقیق `2a5c83bf0ba924f4a2c8f206338a04872562d72d` با موفقیت پایان یافته‌اند: tests [#41239355370](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355370)، dashboard/browser smoke [#41239355374](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355374)، و dependency audit [#41239355466](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41239355466). تست‌های Windows storage نیز در workflow تست موفق بوده‌اند.


## 2026-11-01 — Engineering delivery commitment

- Current main baseline: `348be1d2bd9f69e91f9631911f889e8ab6f442d2` (PR #672 merged).
- The engineering-only delivery baseline is **22 working days / 176 engineering hours**, with target date **2026-12-01** from a planned 2026-11-02 start. The calculation, work-package hours, scope boundaries, completion definition and reforecast rules are in [`docs/ENGINEERING_DELIVERY_ESTIMATE.md`](ENGINEERING_DELIVERY_ESTIMATE.md).
- This schedule excludes local/manual owner testing, human adjudication, target-host operator acceptance and the independent security review itself. Automated tests/CI and code remediation remain in scope.
- The earlier 12-day estimate is withdrawn as unsupported. The historical 60.47% gate score is not an effort estimate.
- The 22-day figure is the preserved planning baseline; any delay must be explained with task-level evidence and actual/remaining engineering hours in `docs/ENGINEERING_LOG.md`. Do not overwrite the original baseline when reforecasting.

## 2026-10-31 — اصلاح یکپارچگی URL در جست‌وجوی منابع (PR #676)


- مبنای PR: شاخهٔ `main` در SHA `bcb04de842faab596d31c3955fa5046a407c9fc7`؛ هیچ تغییری مستقیم روی `main` انجام نشده است.
- در parser نتایج DuckDuckGo، مقدار `uddg` ابتدا با `parse_qs` decode می‌شد و سپس دوباره با `unquote`. این decode دوم می‌توانست `%2B` و `%26` موجود در query مقصد را به داده/جداکننده‌های متفاوت تبدیل کند. اصلاح در `app/web_sources.py` و regression یکپارچهٔ parser/normalizer در `tests/test_web_sources.py` قرار دارد.
- PR [#676](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/676) در وضعیت draft است. commit پیاده‌سازی `f0ff075e9474817cd6fdf643f30087c98b93761f`، commit تست `1b1f0229cd62b3109928da3455a445992d209d7f` و checkpoint مستندات `d08bbc3e25114bc76baa8ccbb4a661f371fa7d54` هستند. پس از checkpoint مستندات، SHA نهایی branch باید دوباره از GitHub خوانده شود.
- CI روی head پیش از مستندات: dependency audit، dashboard smoke و Windows storage موفق؛ test workflow در حال اجرا بود. این نتیجه برای head نهایی پس از مستندات معتبر نیست؛ CI دقیق همان head باید دوباره تأیید شود. اجرای محلی pytest در این runtime انجام نشده است.
- این اصلاح یکپارچگی query URL را حفظ می‌کند؛ منبع‌یابی زنده، دسترس‌پذیری providerها و ارزیابی زندهٔ مدل را اثبات نمی‌کند. ارزیابی pinned-model و human adjudication همچنان باز هستند؛ Issue #52 نیز نیازمند independent security review است.

## 2026-11-01 — Initial local autonomous engineering proposal (PR #675; superseded by PR #680)

- The former paid API dependency is being replaced with Codex CLI local OSS mode through Ollama on a Windows self-hosted runner. No OpenAI API key is part of the proposed activation path.
- New local operations console: `/autonomous-engineering`; it reports Ollama/model/Codex/Runner/GitHub CLI readiness and exposes explicit workflow dispatch/cancel controls.
- Main dashboard, AI-learning page, and new operations page share the primary navigation.
- Workflow architecture: trusted-main local coding job; GitHub-hosted secret-free patch validation; separate GitHub-hosted draft-PR publication. No direct main writes, auto-merge, or deployment.
- Status: implementation branch active; tests and exact-head CI pending. Owner-side Windows runner registration, Ollama/Codex setup, and target-host soak have not been performed and are not claimed complete.


## 2026-11-01 — Local Ollama autonomous engineering console (PR #680)

- Implementation: [PR #680](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/680), branch `automation/codex-engineering-agent-rebased`, exact head `e3c8702db7eb21689f15f01232dca7c76ea137c7`, based on current `main` SHA `60597e13e70a3014f6a1871290cf843d188ca45e`. PR is open, mergeable, and not merged.
- Feature includes local Ollama/Codex readiness diagnostics, explicit workflow dispatch/cancel, model and daily-frequency configuration, live Codex plan/workflow phase progress, evidence-based scorecard history charts, bounded telemetry, and Windows Runner/ACL setup checks.
- Exact-head GitHub Actions all passed: tests + Windows storage regressions [#41269501876](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501876), Chromium dashboard smoke [#41269501826](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501826), dependency audit [#41269501856](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41269501856). All jobs report success.
- Owner-side local installation/testing and human acceptance are intentionally not performed in this engineering pass. Still required before operational use: install/register the Windows self-hosted Runner as a non-admin service identity, configure ProgramData permissions for the exact Runner/app accounts, verify Ollama/model access under that identity, and run target-host soak/recovery/rollback checks.
- Independent review for the existing Ollama loopback boundary remains open in [Issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52). Do not mark it complete or close it based on implementation tests.
- Project-wide acceptance remains below 100%; this feature does not close the remaining UI parity, target-host, live-model/human adjudication, staging/rollback, or independent-review gates. No score increase is claimed.

## 2026-11-01 — Autonomous engineering console: final implementation checkpoint (PR #680)

- Added the direct task-entry form and workflow-dispatch input, with a 2,000-character server/workflow limit, NUL-byte rejection, non-shell argument forwarding, untrusted-task treatment, and an explicit warning not to submit secrets because GitHub records workflow input metadata.
- Hardened the progress telemetry ACL setup so inheritance removal and explicit restricted permissions are applied recursively to existing descendants as well as the root directory. Added regression coverage.
- Final verified PR head: 016f0004afa7443660868d0ba93eca9fdad7f649. Exact-head CI is green: 1,842 passed / 49 deselected; Windows storage regressions passed; browser E2E (including manual-task form submission) passed; dependency audit passed. [Tests](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855384) · [Browser](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855370) · [Dependency audit](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41312855311).
- PR [#680](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/680) remains unmerged and targets main. Local owner tests and human acceptance are deliberately deferred. Remaining operational gates: register/configure the Windows self-hosted Runner under a non-admin account, configure exact app/Runner ACL identities, verify Ollama/model access as that account, and perform target-host soak/recovery/rollback. Issue [#52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) still requires an independent reviewer.
- Project-wide completion is not claimed. Existing human, target-host, staging/rollback, UI-parity, and independent-review gates remain open; scorecard values are not increased by this feature alone.



## 2026-11-06 — Autonomous engineering reliability fixes in review

- PR [#701](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/701) contains code-level fixes for durable manual-task persistence/recovery, safe rejection of task replacement while work remains unaccepted, resume behavior after removing the obsolete failure-streak variable, and active-run visibility/cancellation parity with the workflow schedule gate.
- The implementation had exact-head CI evidence at SHA `c8379f694c503a32ecabab0f53ff306fc26f840c`: 1,899 tests passed / 50 deselected; Windows storage regressions, browser smoke, dependency audit, artifact validation, Markdown links, import-cycle scan, and deterministic benchmarks passed. A subsequent test-only assertion correction requires fresh exact-head CI before readiness is declared.
- PR #701 remains open and unmerged. The separate real Windows service-account Codex sandbox smoke remains an external blocker tracked in PR [#699](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/699); CI on GitHub-hosted runners is not evidence of successful execution on the self-hosted Runner.

## 2026-11-07 — Manual-task persistence clean-workspace correction

- The task-persistence step now runs only after a pinned checkout of trusted `main`, so its PowerShell script is present on a fresh/clean Runner workspace. The checkout does not persist Git credentials.
- The persistence step remains before Python/Ollama/Codex preflight. A regression assertion protects this order.
- Follow-up commits: implementation `e1d47d05bc30cf536e1f8a98bd8e272d4590b53a`; test `0745b50e30a38a08421d6083af8b16eaa750e0c0`. Exact-head CI and target-runner verification remain pending.


- تنظیمات LLM: provider مستقل Ollama یا OpenAI-compatible برای چت، برنامه‌ریزی پژوهش، یادگیری، برنامه‌نویسی، بازبینی factual و embedding؛ انتخاب مدل/endpoint از داشبورد، API key فقط از environment و حالت موقت تا restart یا ذخیرهٔ دائمی. اگر همهٔ laneها جایگزین شوند Ollama از bootstrap حذف می‌شود. انتقال prompt/context به endpoint غیرمحلی فقط پس از انتخاب صریح provider انجام می‌شود. CI exact-head فعلی در حال اجراست و تا پایان آن موفق اعلام نمی‌شود.


### اصلاح تکمیلی منابع یادگیری — 2026-11-11

- بازبینی خواستهٔ مالک نشان داد حداقل سه صفحه از دو دامنه، مانع شروع یادگیری در موضوعات کم‌منبع یا منابع غیرقابل‌دسترسی می‌شد. این شرط سخت حذف شد؛ صفر، یک یا چند منبع قابل‌استفاده دیگر مانع ساخت دوره نیستند.
- همهٔ منابع مرتبطِ قابل‌استفاده، فارغ از رسمی‌بودن یا اعتبار ناشر، با provenance نگهداری می‌شوند. سطح اعتبار، تازگی، تعارض و کمبود شواهد باید جداگانه برچسب بخورند و دلیل حذف داده نباشند.
- وقتی منبعی موجود نیست، دوره با دانش مدل ادامه می‌یابد و وضعیت آن unverified می‌ماند؛ نبود منبع به‌عنوان صحت تأییدشده تفسیر نمی‌شود.
- تغییرات در app/learning_curriculum_tasks.py، آزمون‌های tests/test_learning_curriculum_tasks.py و راهنمای docs/LEARNING.md ثبت شده‌اند. آزمون‌های خودکار exact-head و تست میزبان هنوز اجرا/تأیید نشده‌اند.

- اصلاح تکمیلی: سقف نگهداری ۴۰ منبع و سقف سه منبع برای هر دامنه نیز حذف شد؛ تمام نتایج قابل‌استفادهٔ برگشتی از هر query با URL/hash/provenance حفظ می‌شوند. محدودیت excerptهای ارسالی به هر فراخوانی مدل صرفاً بودجهٔ context همان درخواست است و به معنی حذف منبع از سابقهٔ دوره نیست. آزمون چهار منبع از یک دامنه در هر query اضافه شد.

- بازبینی تمام call-siteهای سقف منبع، یک reference قدیمی در پایش انتشار را آشکار کرد که پس از حذف constant می‌توانست در اولین نتیجهٔ معتبر خطای NameError ایجاد کند. شرط‌های باقی‌مانده حذف شدند؛ تست‌های موجود baseline/change/deduplication پایش انتشار این مسیر را پوشش می‌دهند و باید روی exact-head اجرا شوند.

- بازبینی دوم checkpointها نشان داد یک برش قدیمی با سقف ۴۰ منبع در مسیر بازیابی باقی مانده بود و هنگام resume خطا ایجاد می‌کرد. آن هم حذف شد و آزمون بازیابی ۴۵ منبع با provenance query اضافه شد تا کل فهرست منابع پس از restart حفظ شود.

- شرط باقی‌ماندهٔ «فقط در صورت تأیید منبع رسمی» از prompt تغییرات نسخه‌ای حذف شد؛ گزارش‌های غیررسمی نیز جمع‌آوری و به منبع خود منتسب می‌شوند و در صورت نبود corroboration به‌صورت unverified باقی می‌مانند. آزمون جلوگیری از بازگشت این فیلتر اضافه شد.


### 2026-11-11 — قرارداد یادگیری عمومی و ترتیب نسخه‌ها

- قرارداد جامع یادگیری مستقل از موضوع به master prompt و شاخهٔ PR افزوده شد: سرفصل‌بندی کامل، تاریخچهٔ نسخه‌ها از قدیمی‌ترین تا جدیدترین، حفظ منشأ داده‌ها، ارزیابی غیرحذفی، اصلاح خودکار نسخه‌دار، پایش انتشار و قابلیت جابه‌جایی LLM از تنظیمات.
- یک نقص واقعی در ترتیب نسخه‌ها بین batchها پیدا و اصلاح شد. اعتبارسنجی قبلی فقط هر batch را جداگانه بررسی می‌کرد و ترتیب کل دوره پس از ادغام batchها تضمین نمی‌شد. اکنون کل سرفصل‌ها پیش از ذخیره و صف‌بندی درس‌ها از نظر ترتیب انتشار بررسی می‌شوند.
- آزمون رگرسیون برای حالت batchهای جداگانه معتبر ولی ترتیب کلی نامعتبر اضافه شد. نتیجهٔ CI exact-head و اجرای محلی هنوز در انتظار است.

- آزمون نگهبان برای جلوگیری از حذف ناخواستهٔ قرارداد یادگیری عمومی از master prompt اضافه شد. متن assertion با عبارت واقعی سند تطبیق داده و دوباره بررسی شد.


### 2026-11-11 — خودبازبینی درس و ارزیابی معنایی

- پرامپت تولید درس به v6 ارتقا یافت و پیش از تحویل، بازبینی سازگاری نسخه و تاریخ، دستورها/API/کد، پیش‌نیازها، مراحل جاافتاده، تناقض‌ها، عوارض جانبی و خروجی مورد انتظار را الزامی می‌کند. خطای تشخیص‌داده‌شده باید پیش از ارائه اصلاح شود؛ اجرای تست بدون نتیجهٔ واقعی ادعا نمی‌شود.
- ارزیابی پاسخ بر صحت معنایی و استدلال تکیه می‌کند؛ بازنویسی معتبر و راه‌حل جایگزین درست پذیرفته می‌شود و پاسخ ناقص از پاسخ غلط تفکیک می‌شود.
- registry، آزمون‌ها و مستندات برای `learning-lesson-v6` به‌روزرسانی شدند؛ نسخهٔ repair به‌دلیل عدم تغییر متن همان v5 باقی ماند.
- تطبیق ارجاع‌های نسخه‌ای انجام شد؛ CI دقیق آخرین commit و اجرای مدل روی میزبان هدف هنوز تأیید نشده است.


### وضعیت به‌روزشدهٔ موارد باز یادگیری

- **ارزیابی معنایی:** rubric مفهومی مبنای امتیاز قطعی است؛ semantic reviewer مستقل در صورت پیکربندی می‌تواند پاسخ‌های معنایی/بازنویسی‌شده را ارزیابی کند. عدم دسترسی، خروجی نامعتبر یا ابهام reviewer نباید baseline را حذف کند یا پیشرفت را به‌اشتباه کامل کند. اجرای زندهٔ reviewer model روی میزبان مقصد هنوز انجام نشده است.
- **پاسخ‌های چت به‌عنوان دادهٔ آموزشی:** پاسخ‌های assistant در تاریخچهٔ محلی، حتی کوتاه و کم‌کیفیت، با provenance و وضعیت unverified ذخیره می‌شوند؛ پاسخ‌های مرتبط در ساخت syllabus و تولید درس به‌عنوان مشاهدهٔ کمکی وارد می‌شوند. این candidateها خودکار به دانش trusted ارتقا نمی‌یابند و تبدیل مستقیم همهٔ candidateها به سرفصل canonical هنوز گردش‌کار جداگانه‌ای است.
- **اصلاح درس:** گزارش کاربر برای خطای کد/دستور یک job اصلاحی مستقل می‌سازد؛ نسخهٔ قبلی و گزارش حفظ می‌شوند و نسخهٔ اصلاح‌شده افزایشی است. تست اجرایی فقط در صورت وجود محیط امن و ثبت نتیجه قابل ادعاست.


### 2026-11-11 — وضعیت اصلاح خطای CI در PR #710

- خطای `IndentationError` در `app/learning_curriculum_tasks.py:689` که باعث شکست collection تست‌ها و Windows storage شده بود، در commit `ff84957543644d74a04169a1bf13b57f6f6c1b8b` اصلاح شد.
- CI دقیق head جدید شامل workflowهای tests و browser-e2e است که در زمان ثبت این وضعیت queued هستند؛ تا دریافت نتیجهٔ دقیق همان SHA، هیچ pass ادعا نمی‌شود.
- Browser E2E در head قبلی قبل از اجرای برنامه، به‌علت شکست DNS در دریافت بسته‌های Chromium از Ubuntu repositories متوقف شده بود.
- این snapshot تاریخی با PR #711 تکمیل شده است: پاسخ‌های چت اکنون در curriculum و تولید درس به‌عنوان observations غیرقابل‌اعتماد استفاده می‌شوند؛ اصلاح گزارش‌شدهٔ درس به‌صورت job/revision مستقل انجام می‌شود. هیچ candidate به‌طور خودکار trusted نمی‌شود.


### 2026-11-12 — ادامهٔ یادگیری غیرمخرب و صفحه‌بندی observations

- PR #711 در `main` ادغام شده است؛ این تغییرات در شاخهٔ مستقل `feature/non-destructive-autonomous-learning-v3` دنبال می‌شوند.
- قرارداد اصلی اکنون admission دادهٔ آموزشی را از صحت factual، کیفیت درس و mastery یادگیرنده جدا می‌کند: پاسخ غیرخالی، حتی ناقص یا بدون منبع، حذف نمی‌شود؛ وضعیت unverified حفظ می‌شود.
- بازیابی پاسخ‌های قبلی گفتگو اکنون cursor-based است: تطبیق هم در پرسش و هم در محتوای پاسخ انجام می‌شود، پاسخ‌های بلند به قطعه‌های محدود تقسیم می‌شوند و curriculum تا مصرف همهٔ صفحات مرتبط ادامه می‌یابد.
- تست محلی در این محیط اجرا نشده است. تأیید این شاخه منوط به اجرای CI روی head دقیق و بررسی نتیجهٔ تست‌های regression است؛ اجرای زندهٔ مدل روی میزبان کاربر نیز هنوز ادعا نمی‌شود.


### 2026-11-12 — افزایش پوشش پایش انتشار

- پایش دوره‌ای هر یک از پنج query تخصصی انتشار اکنون از حداکثر ۳۰ نتیجهٔ قابل‌دریافت استفاده می‌کند، نه ۵ نتیجه.
- regression جدید تغییر release note در نتیجهٔ هفتم را شبیه‌سازی می‌کند؛ پایش باید آن را کشف و یک curriculum refresh نسخه‌دار صف‌بندی کند.
- fingerprint دوره‌های قبلی ممکن است یک‌بار به‌دلیل گسترش نتایج تغییر کند؛ refresh افزایشی است و درس‌های تاریخی را بازنویسی نمی‌کند.
- سقف ۳۰، اندازهٔ صفحهٔ هر query در adapter جست‌وجو است؛ این تغییر ادعای کشف تضمینی همهٔ منابع اینترنتی نیست.


## 2026-11-14 — PR #757: execution ownership, locks, and bounded recovery

- Root architectural defects addressed: Codex/controller descendants could outlive the owner and overlap a retry; durable-state and worktree locks did not themselves own OS process trees; task retry loops and helper commands lacked independent total deadlines; recovery workflow could repeat failures indefinitely.
- Added separate process-tree command runner, Windows Job Object and POSIX process-group primitives, Codex owner-identity supervisor, durable state lock, validated retry policy, and workflow circuit breaker. Checkpoint Git operations are now routed through the same process-tree runner with a 120-second deadline; binary checkpoint patches remain byte-exact.
- Regression coverage includes ownership loss, broken output pipe, supervisor crash with surviving descendants, timeout cleanup, Windows Job Object drain/assignment failure, POSIX process-group drain, checkpoint concurrency/recovery, and binary stdin preservation. Existing tests and assertions were retained.
- Root-cause follow-ups from CI: repaired the temporary POSIX owner harness import path; guarded cleanup when a process test double has no PID; added byte-exact command input and checkpoint-runner delegation tests. Operator docs were corrected to the bounded retry/circuit-breaker policy.
- PR [#757](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/757) remains draft and unmerged. Exact-head CI on `5a8a405e0141be2978a9a92a596d82a32174c73b` passed: 2,240 Python tests passed, 13 skipped, 51 deselected in 176.72 seconds; Windows storage/process/checkpoint regressions, Browser E2E, dependency audit, CodeQL, generated-artifact checks, Markdown-link validation, import-cycle checks, deterministic lexical benchmark, and fail-closed claim-to-test coverage benchmark all passed. The real non-admin Windows Runner sandbox smoke, long-duration soak, crash/restart acceptance, and independent security review remain open.
- Durable Pause remains enabled. No Runner, service, local process, task dispatch, or autonomous execution was started; no task/checkpoint/user data was modified.


## 2026-11-14 — Windows portability status

- Removed the session-scoped named-mutex dependency from durable-state locking; Python and PowerShell now use one cross-session byte-range file lock.
- Removed fixed user-profile and Python-version paths from both Windows autonomous workflows; runtime tools are resolved from the Runner service identity's PATH.
- Moved the default Runner installation root under ProgramData.
- Added regression coverage for shared runtime locking and account-independent tool discovery.
- Pending: exact-head Windows CI, actual cross-account ACL verification on a separate Windows host, and target-host sandbox smoke/soak. The autonomous system remains paused/off; no local execution was started.


## Windows state-root hardening

Windows no longer falls back to a per-user home directory when PROGRAMDATA is missing. Configured state roots must be absolute, preventing different working directories from resolving separate locks/state stores. The existing POSIX state location is preserved.


## Final exact-head CI evidence — 2026-11-14

Exact tested code head: ab703a284752c80531e9f25e0b7ad53a04c453a0.

- Full Python suite: 2,244 passed, 13 skipped, 51 deselected in 243.29 seconds.
- Focused checkpoint ownership/concurrency regressions: passed.
- Windows storage/process/checkpoint regressions: passed, including Python/PowerShell shared file-lock interoperability and PowerShell parser checks.
- Browser E2E, dependency audit, and CodeQL: passed.
- Generated learning artifacts, repository-local Markdown links, import-cycle guard, deterministic lexical retrieval benchmark, and fail-closed claim-to-test coverage benchmark: passed.

Evidence: Tests/Windows regressions https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43629863471 ; Browser E2E https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43629863440 ; Dependency audit https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43629863452 ; CodeQL https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/43629863507 .

The subsequent documentation-only evidence update does not change runtime code and is followed by a fresh exact-head CI run. Target-host cross-account ACL verification, non-admin sandbox smoke/soak, crash/restart acceptance, and independent security review remain pending. Durable Pause remains enabled; no Runner, service, or autonomous process was started.

- Gate-C backend follow-up در PR #817 ادغام شده است (merge commit `a5855e9230f8758f21be91b8586946d195d015f6`): خطاهای API از envelope ساختاریافته و headerهای correlation/retry استفاده می‌کنند؛ داده‌های ورودی و جزئیات exception ناشناس‌سازی می‌شوند و stderr خام GitHub به UI بازگردانده نمی‌شود.


## 2026-11-22 — Gate-C diagnostics and dispatch follow-up (PR #817)

- Windows Runner execution `44932980718` / job `137009509972` failed after two Codex attempts: attempt 2 produced no final `agent_message`, the output-last-message report was empty, and completion-marker validation failed. The durable task was preserved and not marked complete; no artifact was produced.
- اجرای ناموفق Windows Runner به‌علت نبود final `agent_message`، گزارش خالی و عدم ثبت completion marker رخ داد؛ task/checkpoint حذف یا complete نشد.
- PR قدیمی #818 ادغام نشد و بسته شد؛ PR جایگزین #827 در 2026-11-22 با merge commit `0367a3ebfd92ac196bb096f1a45cbfed5a61985d` ادغام شد و بازیابی محدود از JSONL معتبر را اضافه کرد. این ادغام، خطای اجرای واقعی Codex روی Windows را به‌تنهایی اثبات‌شده حل‌شده نمی‌کند.
- PR #834 اصلاحات UI feedback، حفاظت cleanup با repository lock، تست HTTP برای producer واقعی خطای API و رفع بسته‌بندی importهای `scripts` را دنبال می‌کند؛ CI exact-head جدید و Gateهای عملیاتی هنوز باز هستند.


## وضعیت اعتبارسنجی زنده — 2026-11-22

- PR #834 در head `02ede5f4ccd20aace3d8e2db531d0998bd842dc3`: Browser E2E، CodeQL، Analyze، pip-audit و سه bootstrap موفق؛ Linux full suite با ۳ failure و Windows storage regressions با ۲ failure. Full Windows suite هنگام ثبت هنوز در حال اجرا بود.
- اصلاح Windows byte-range lock و دو اصلاح تست در branch جایگزین `fix/autonomous-engineering-first-module-validation-20261122` از همان head آماده شده‌اند؛ قبل از بازکردن PR جایگزین، CI دقیق اجرا نشده است.
- PR #823 نیز اصلاح نهایی pause/cancel هنگام ثبت وضعیت discovery را در branch خود دارد؛ CI دقیق آن اجرا نشده است.
- هیچ‌یک از این تغییرات merge نشده‌اند. اجرای Runner/Ollama/Codex واقعی، crash/restart و تأیید Windows target و مالک همچنان gateهای باز هستند.


## 2026-11-23 — exact-head CI نتیجه و وضعیت پذیرش محلی

- کد اصلاح‌شده در SHA `d993038033d5d15cca4a4a6c63c979c081c89aad` روی PR #846 بررسی شد؛ همهٔ workflowهای CI روی همین SHA موفق شدند.
- Ubuntu: 2,555 passed، 14 skipped، 78 deselected در 285.03 ثانیه؛ run #45131387511.
- Windows: 2,550 passed، 19 skipped، 78 deselected در 677.79 ثانیه؛ run #45131387511.
- Windows storage regressions: موفق؛ parse همهٔ PowerShellها، کامپایل Broker، ACL بازگشتی، LocalService named-pipe، هویت Windows، checkpoint/lock و SQLite restore؛ run #45131387511.
- Browser E2E: موفق؛ صفحه و ۶ فایل UI با HTTP 200، بدون console/page/failed-request/unexpected-HTTP errors؛ run #45131387286، artifact #14797757907.
- CodeQL: موفق، run #45131387204. Dependency audit: موفق، run #45131387210. Python bootstrap در Windows/Ubuntu/macOS نیز موفق، run #45131387511.
- PR #847 صرفاً mirror موقت برای اجرای workflowهای PR بود؛ پس از تکمیل بررسی بسته شد و branch آن حذف نشد. PR اصلی #846 همچنان Draft و باز است.
- این به‌روزرسانی مستندات پس از CI فقط وضعیت و شواهد را ثبت می‌کند؛ هیچ کد اجرایی پس از SHA d993 تغییر نکرده است.
- پذیرش روی Windows واقعی کاربر هنوز pending است: اجرای عادی از حساب غیرادمین، UAC، Broker با Runner موجود و فایل .runner غیرقابل‌خواندن، نبود Runner، UI محلی، Ollama/Codex و dispatch واقعی. تا ثبت این شواهد، PR #846 merge نمی‌شود.

---

## 2026-11-24 — سخت‌سازی انتخاب هدف Cancel

- Cancel اکنون از همان dispatch lock مشترک با Dispatch و Resume استفاده می‌کند؛ در زمان ثبت Dispatch/Resume هم‌زمان اجرا نمی‌شود.
- انتخاب اجرا قطعی‌تر شد: یک Codex job فعال، هدف اصلی است؛ چند Codex job فعال یا چند candidate صف‌شده باعث 409 و fail-closed می‌شود. اگر job مشخصی وجود نداشته باشد، فقط یک workflow فعالِ یکتا قابل لغو است؛ چند workflow مبهم هرگز با انتخاب جدیدترین run لغو نمی‌شوند.
- تست‌های regression برای چند Codex job فعال، چند candidate صف‌شده، fallback مبهم، تنها workflow فعال و lock contention اضافه شدند. این اصلاح در شاخه‌های والد و follow-upهاست؛ exact-head CI هنوز pending است.

## 2026-11-24 — Autonomous Engineering module hardening in progress

- Work is isolated on fix/autonomous-engineering-backend-ui-hardening-20261124-rebased and is stacked on the latest live-pause base branch fix/recovered-pr-chain-live-pause-gate-20261124 (PR #870).
- The implementation covers the 14 reported backend, Codex retry/report, and UI findings. Windows dispatch admission now goes through the protected State Broker rather than opening protected state files from the application account. Windows cleanup delegates active-progress and lock decisions to the broker.
- The broker protocol includes strict two-phase dispatch reservation operations for both POSIX and Windows. These changes require the Windows service to be rebuilt/reinstalled before target-host validation.
- Tests and documentation have been updated, but exact-head CI has not yet been run on this branch. Do not merge or claim the module is fully fixed until Linux and Windows suites, broker service compilation/integration, browser E2E, and real Windows target-host acceptance checks pass.
- Existing active workflow runs were left untouched. No active run was cancelled or modified.

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
- نتیجه: Gateهای repository CI و Browser E2E روی این SHA موفق‌اند؛ ماژول هنوز به‌عنوان operationally accepted یا ready-to-merge اعلام نمی‌شود. PR تا تکمیل گیت‌های میزبان Draft می‌ماند.
