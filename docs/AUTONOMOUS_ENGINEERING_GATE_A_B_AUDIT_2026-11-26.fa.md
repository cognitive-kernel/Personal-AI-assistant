# ممیزی اجرایی Gate A و Gate B ماژول Autonomous Engineering — 2026-11-26

## دامنه و مبنای شواهد

این گزارش بر اساس دو سند حاکم زیر و کد زندهٔ GitHub تهیه شده است:

- `docs/MASTER_ENGINEERING_PROMPT.md` در ref `docs/master-engineering-continuity-prompt`، blob `0fc04693a3abfd0cf2fe6cf865f268f00967751d`.
- `docs/MASTER_UI_ENGINEERING_PROMPT.fa.md` در ref `docs/ui-engineering-first-module-20261121`، blob `b778c91fff53f6093db1fd6c8b2ad6dbdf82d130`.

مبنای زندهٔ `main`: `8b7c20c50ef4fd9346e924d212a35f36ff646515`.

زنجیرهٔ PR جاری:

| مورد | وضعیت زنده |
|---|---|
| PR #872 | باز و Draft؛ base=`main`؛ head=`18bcba48e843a5920e27c33f51f4bf98ec1728aa`؛ 83 فایل تغییرکرده |
| PR #872 حجم diff | 10,707 additions / 1,375 deletions؛ 495 commit در مقایسه با main |
| CI روی head شمارهٔ 872 | tests، browser-e2e، CodeQL و dependency-audit موفق |
| PR #877 | باز و Draft؛ base=شاخهٔ PR #872؛ head=`4d410fbed6ff1333402c500b80dc6f17c8e85d3c`؛ 20 فایل تغییرکرده |
| CI روی head شمارهٔ 877 | browser-e2e و dependency-audit موفق؛ tests در زمان ممیزی queued بود |
| پذیرش واقعی Windows target | انجام‌نشده؛ از CI یا تست mock قابل استنتاج نیست |

## Gate A — نقشهٔ UI تا اجرا

ممیزی موجود `docs/UI_AUTONOMOUS_ENGINEERING_LIVE_AUDIT_2026-11-24.fa.md` این inventory را ثبت کرده است:

- صفحهٔ مستقل: `/autonomous-engineering`.
- 7 asset/module تخصصی UI شامل HTML، CSS و JavaScriptهای feature.
- 13 API route اختصاصی ماژول.
- 21 تست Browser E2E با نام Autonomous Engineering.
- workflow مرورگر، تست‌های marked را روی Chromium و backend ایزوله اجرا می‌کند؛ این تست‌ها جایگزین Runner/Ollama/Codex واقعی نیستند.

### جدول مسیرها و مالکیت state

| عملیات | مسیر اصلی | مالک authoritative | قفل/اعتبارسنجی | وضعیت شواهد |
|---|---|---|---|---|
| بارگذاری و readiness | Browser → status API → Broker/GitHub/Ollama probes | سرویس‌های مالک هر capability؛ UI فقط projection | timeout و bounded probes؛ UI نباید منبع حقیقت باشد | قرارداد و browser coverage موجود؛ fault matrix میزبان کامل نیست |
| ارسال درخواست | UI → dispatch API → Broker admission → workflow_dispatch | Broker برای admission؛ GitHub برای workflow | dispatch admission reservation و بررسی server-side | مسیر کد موجود؛ اجرای واقعی از مرورگر تأیید نشده |
| ذخیرهٔ task دستی | workflow → `persist_autonomous_manual_task.ps1` | فایل task و owner record در shared state | GitHub concurrency + `autonomous-durable-state.lock`؛ نوشتن اتمیک | کد و تست موجود؛ رقابت چند process روی Windows target لازم است |
| capture task | workflow → Python capture step | فایل durable task | shared durable-state lock؛ تطبیق متن task و owner record | capture فقط‌خواندنی نسبت به متن task است؛ CI به‌تنهایی اجرای چندحسابی را ثابت نمی‌کند |
| شروع/ادامهٔ چرخه | workflow → `autonomous_engineering_cycle.py` | controller و durable task/PR records | `repository_execution_lock`؛ بررسی مجدد Pause/Mode پس از admission | قفل process-level و live settings check در کد وجود دارد؛ orphan process و crash/restart روی میزبان باز است |
| checkpoint | cycle → watcher → `autonomous_engineering_checkpoint.py` | checkpoint store | `checkpoint_store_lock` در طول عمر watcher؛ metadata/index و patch verification | atomic/index checks موجود؛ تست واقعی crash و process orphan روی Windows باز است |
| پیشرفت و diagnostics | worker → shared state → Broker → UI | فایل‌های telemetry/diagnostics؛ غیر-authoritative برای task ownership | نوشتن اتمیک فایل و محدودیت اندازه | UI projection و sanitization وجود دارد؛ رقابت writerها و fault injection کامل باید بررسی شود |
| ساخت PR | Codex → publisher child process → GitHub | pending PR record + GitHub PR | parent repository lock، durable-state lock، exact head/base checks | workflow و قرارداد کد موجود؛ اجرای واقعی Codex و publish روی میزبان باز است |
| recovery | recovery workflow → GitHub API → cycle | pending task/PR/checkpoint | retry policy، run identity، live Pause/Mode، durable locks | مسیر کد موجود؛ وابستگی credential و crash/recovery واقعی باز است |
| تکمیل و پاک‌سازی | owner merge → post-merge checks → cleanup intent | pending task/owner/PR/checkpoint | exact merge SHA، post-merge CI، cleanup marker و durable lock | fail-closed و cleanup intent موجود؛ تست عملیاتی روی میزبان باز است |
| Pause/Resume/Cancel | UI → API → GitHub settings/workflow | تنظیمات مخزن و run/job فعال | live Pause/Mode بعد از concurrency؛ cancel باید Codex job صحیح را هدف بگیرد | کد و browser tests موجود؛ اعتبارسنجی واقعی تنظیمات و runهای زنده باز است |

## Gate B — یافته‌های مالکیت، lease، lock و recovery

### یافته‌های تأییدشده

1. **چند حوزهٔ مالکیت جدا وجود دارد و نباید با هم ادغام شوند.**
   - `repository_execution_lock` مالکیت worktree را نگه می‌دارد.
   - `durable_state_lock` تراکنش فایل‌های task/PR را بین Python و PowerShell سریال می‌کند.
   - `checkpoint_store_lock` مالکیت watcher و تغییر checkpoint را جداگانه کنترل می‌کند.
   این جداسازی در ADR-015 تعریف شده و باید حفظ شود؛ جایگزینی همهٔ آن‌ها با یک lock یا thread lock، regression معماری خواهد بود.

2. **Task owner sidecar هنوز schema v1 است.**
   - فایل `pending-manual-task-owner.json` شامل `owner_run_id`، `owner_attempt`، `claim_count`، زمان‌های ایجاد/claim و digest محتوا است.
   - Python، PowerShell و Windows Broker همگی schema v1 را اعتبارسنجی می‌کنند.
   - اعتبارسنجی worker فعلی run ID و attempt را تطبیق می‌دهد؛ `claim_count` در owner record ذخیره می‌شود، اما در قرارداد mutation به‌عنوان fencing epoch صریح حمل و مقایسه نمی‌شود.
   - این مورد هنوز یک exploit اثبات‌شده نیست؛ اما با الزام Master Prompt برای owner + fencing token/epoch + expiry هم‌ارزی اثبات‌شده ندارد. پیش از تغییر schema باید تمام writerها، readerها، recoveryها و mutationها در Python/PowerShell/C#/POSIX فهرست شوند و migration سازگار با داده‌های قدیمی طراحی شود.

3. **قفل worktree OS-level است، نه distributed lock.**
   - Windows از byte-range lock و POSIX از `flock` استفاده می‌کند.
   - این طراحی به یک Windows Runner یکتا با label `autonomous-state-owner` وابسته است؛ این وابستگی با state محلی ProgramData توجیه شده و نباید با حذف label به چند میزبان مستقل گسترش یابد.
   - Runner workflow همچنین به labelهای `self-hosted`، `Windows`، `X64` و `ollama` نیاز دارد. وجود این labelها در میزبان واقعی از طریق این اتصال تأیید نشده است.

4. **وابستگی Secret برای خواندن Repository Variables صریح است.**
   - workflow مهندسی و recovery به `AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN` با دسترسی read-only به Variables نیاز دارند.
   - نبود آن اجرای task را fail-closed متوقف می‌کند.
   - مقدار secret قابل مشاهده نیست؛ بنابراین وجود یا نبود آن در repository تأیید نشده است. نباید بدون بررسی، آن را «مفقود» اعلام کرد یا حذف کرد؛ جایگزین باید بتواند live Pause/Mode را پس از concurrency admission بدون پنجرهٔ race اثبات کند.

5. **پذیرش عملیاتی هنوز باز است.**
   - سبز بودن چهار workflow روی head #872 و Browser E2E روی head #877 اثبات نمی‌کند Runner service، هویت/ACL، Ollama، مدل انتخاب‌شده، Codex tool call، checkpoint و recovery واقعی کار می‌کنند.
   - head #877 در زمان این ممیزی هنوز تست workflow خود را در وضعیت queued داشت.

### بدهی معماری و modularization ناقص

PR #877 چند تابع کمکی و projection خالص را استخراج کرده، اما این کار به‌تنهایی مرزهای capability را کامل نکرده است. در head فعلی #877:

| فایل | خطوط | مسئولیت/ریسک باقی‌مانده |
|---|---:|---|
| `app/autonomous_engineering.py` | 1,153 | هنوز route orchestration، status, dispatch, controls, GitHub diagnostics و cleanup را کنار هم دارد |
| `scripts/autonomous_engineering_cycle.py` | 1,136 | هنوز چرخهٔ اجرا، retry، checkpoint coordination، diagnostics، PR publication و recovery را هماهنگ می‌کند |
| `scripts/autonomous_engineering_checkpoint.py` | 719 | watcher، persistence، index، restore و cleanup |
| `scripts/autonomous_engineering_progress.py` | 679 | progress، Codex process supervision، heartbeat، snapshot و telemetry |

این اعداد به‌تنهایی معیار شکست نیستند؛ اما مسئولیت‌های مستقل داخل این فایل‌ها باید با dependency/call-site map و tests جدا شوند. استخراج صرفاً تابع‌های pure، مالکیت و coupling مسیرهای اصلی را به‌طور کامل حل نکرده است.

### وابستگی‌های اجرایی و پیش‌نیازها

- تغییر PR #872 در `run.py` startup را برای Broker و Ollama read-only کرده است؛ startup نباید به‌طور خودکار سرویس را نصب/راه‌اندازی یا مدل را pull کند.
- این رفتار با ممنوعیت mutation ناخواسته در startup سازگار است؛ اما مسیر تأمین خودکار dependency ضروری هنگام استفاده از capability هنوز باید تکمیل و با رضایت/سیاست کاربر، hash/signature، cache، rollback و failure isolation اثبات شود.
- تست‌های قدیمی auto-pull/startup به تست‌های عدم mutation در startup تبدیل شده‌اند. این تغییر نباید به حذف قابلیت تأمین dependency منجر شود؛ provisioning باید در مسیر صریح و capability-scoped خودش پوشش داده شود.
- workflow از PowerShell برای مراحل Windows استفاده می‌کند و برخی مراحل به CLIهای بیرونی وابسته‌اند. ماتریس Windows PowerShell 5.1، PowerShell 7 و مسیرهای بدون PowerShell هنوز با شواهد target-host بسته نشده است.
- نصب دقیق GitHub CLI در workflow باید از نظر ضرورت pin دقیق در برابر بازهٔ سازگاری آزموده‌شده بازبینی شود؛ نسخهٔ دقیق نباید بدون دلیل سازگاری/امنیتی به پیش‌نیاز مصرف‌کننده تبدیل شود.

## Root-cause register و ترتیب اصلاح

| شناسه | شدت | یافته | اقدام لازم برای بستن |
|---|---|---|---|
| AE-ROOT-01 | بحرانی/باز | مسیر واقعی UI → Runner → Codex → PR روی میزبان مالک هنوز اثبات نشده است | task غیرمخرب end-to-end و artifactهای correlation/run/job/PR/checks |
| AE-ROOT-02 | بالا | task owner schema v1 فاقد fencing epoch صریح در قرارداد mutation است | inventory تمام mutationها، characterization tests، طرح migration v2 و stale-owner rejection در تمام adapterها |
| AE-ROOT-03 | بالا | dependency secret می‌تواند تمام اجرای remote را fail-closed متوقف کند؛ وجود آن نامعلوم است | تأیید وجود/scope توسط مالک یا طراحی جایگزین بدون race و تست‌شده |
| AE-ROOT-04 | بالا | PR #877 modularization را کامل نکرده و فایل‌های هماهنگ‌کننده هنوز چندمسئولیتی‌اند | استخراج مرحله‌ای application services و adapters با حفظ facade/compatibility seams |
| AE-ROOT-05 | بالا | provisioning dependencyهای capability-specific بعد از read-only startup کامل اثبات نشده است | مسیر on-demand acquisition با رضایت، verification، cache، rollback و fault tests |
| AE-ROOT-06 | بالا | adapterهای PowerShell/CLI و Runner labels به‌صورت واقعی روی target اثبات نشده‌اند | ماتریس Windows 5.1/7/no-PS، identity، path و label acceptance |
| AE-ROOT-07 | بالا | تست workflow head #877 هنوز queued بود | exact-head CI کامل روی SHA نهایی |

## قواعد جلوگیری از regression

- هیچ run، task، checkpoint، branch، PR، secret، repository variable، Runner، ACL یا مدل محلی حذف/لغو/بازنویسی نشود.
- هیچ test، assertion یا safety check برای سبزشدن CI حذف یا ضعیف نشود.
- migration task owner فقط additive و تحت lock انجام شود؛ فایل‌های v1 و backupهای آن تا اثبات migration سالم حفظ شوند.
- ابتدا characterization testهای stale owner، retry، crash و concurrent dispatch اضافه شوند؛ سپس تغییر implementation.
- هر تغییر در workflow/PowerShell باید با تست parser و رفتار Windows 5.1/7 پوشش داده شود.
- PRها Draft باقی بمانند تا exact-head CI، Windows target acceptance، crash/restart/recovery و پذیرش UI مالک ثبت شوند.

## وضعیت Gateها در این snapshot

- Gate A: inventory ایستا و جدول مسیر/state ثبت شد؛ reproduction تعاملی همهٔ کنترل‌ها روی مرورگر واقعی هنوز باز است.
- Gate B: مرز lockها و mutationها مستند شد؛ اثبات fencing/recovery در target و تعیین تکلیف schema v1 هنوز باز است.
- Gate C تا K: باز یا partially verified؛ هیچ‌کدام صرفاً با این گزارش بسته نمی‌شوند.
