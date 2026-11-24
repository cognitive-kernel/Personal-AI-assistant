# ممیزی زنده ماژول Autonomous Engineering از UI تا اجرا

- تاریخ بررسی: 2026-11-24
- مخزن: `cognitive-kernel/Personal-AI-assistant`
- شاخهٔ مبنا: `main`
- SHA دقیق مبنا: `8a879d0962b00897744e114ff7297e5c6228ff1d`
- هدف: تکمیل قابلیت عملیاتی از رابط وب `/autonomous-engineering` تا Ollama/Codex، Runner، GitHub Actions، PR و recovery.
- این گزارش بر کد و شواهد GitHub متکی است؛ اجرای UI روی رایانهٔ واقعی مالک در این اتصال انجام نشده است.

## 1. وضعیت زنده و ایمنی عملیات

### main و Actions

در زمان snapshot، آخرین `main` برابر `8a879d0962b00897744e114ff7297e5c6228ff1d` بود. از 11 check روی همین SHA:

- 10 check تکمیل و موفق بودند: `schedule-gate`، تست‌های کامل، تست کامل Windows، Windows storage regressions، browser dashboard smoke، Analyze، pip-audit و سه bootstrap مربوط به Windows/macOS/Ubuntu.
- check با نام `codex` در workflow اجرای مهندسی هنوز `queued` بود.
- workflow run شمارهٔ `45413836616` (run number 375) روی همین SHA در صف اجرای job مهندسی قرار داشت.

**قید ایمنی:** این اجرای زنده و state احتمالی مرتبط با آن نباید برای آزمایش UI لغو، جایگزین، پاک‌سازی یا دور زده شود. هر تست عملیاتی جدید باید پس از تعیین تکلیف اجرای فعلی و با task آزمایشی غیرمخرب و مجاز انجام شود.

### PRهای باز و وابستگی‌ها

| PR | head | base | وضعیت زنده در زمان بررسی | شواهد |
|---|---|---|---|---|
| #863 | `bb12db73565775e999f88c3cb354af94400031e5` | `main` قدیمی: `3d1e843ff1d2726e74e16535cdff3ee3c1da44ea` | Draft، mergeable=false | برای head فعلی check-run ثبت نشده |
| #869 | `e2bc29a84651a5060754b967b766d55c7e9d7707` | شاخهٔ #863 | Draft، mergeable=false | برای head فعلی check-run ثبت نشده |
| #866 | `242f39e4f904d3ca31643f00ba6da763e0265049` | `main` قدیمی: `3d1e843ff1d2726e74e16535cdff3ee3c1da44ea` | Draft، mergeable=false | برای head فعلی check-run ثبت نشده |
| #870 | `b67aa237ba54428cfbeed2813b768031beef3f8d` | شاخهٔ #866 | Draft، mergeable=false | برای head فعلی check-run ثبت نشده |
| #872 | `476bcdc76c777457131224c017ea48520fe1ccee` | شاخهٔ #870 | Draft، mergeable=true نسبت به base خودش | 8 check روی exact head موفق |

PR #872 شامل 34 فایل تغییرکرده و در حال حاضر روی زنجیرهٔ #870 → #866 قرار دارد؛ #870 خود بر #866 و #869 بر #863 متکی است. مقایسهٔ head #872 با main فعلی وضعیت `diverged`، تعداد 289 commit جلو و 3 commit عقب، و merge-base برابر SHA قدیمی `3d1e843...` را نشان می‌دهد. بنابراین سبز بودن CI آن به معنی آماده‌بودن برای ادغام مستقیم در main نیست. این زنجیره نباید با rebase/merge شتاب‌زده یا حذف PRها شکسته شود؛ ابتدا باید ترتیب وابستگی‌ها و تغییرات متداخل بررسی و سپس به‌صورت کنترل‌شده به main فعلی منتقل شوند.

8 check موفق PR #872 شامل full test suite، full test suite ویندوز، Windows storage regressions، browser dashboard smoke، سه bootstrap پلتفرمی و pip-audit است. workflow مربوط به `browser-e2e.yml` دستور `pytest -q -m browser_e2e tests/test_browser_e2e.py` را اجرا می‌کند؛ این شواهد browser automation در CI است، نه پذیرش روی رایانهٔ مالک.

## 2. موجودی فعلی UI

صفحهٔ مستقل فارسی و RTL در `app/static/autonomous-engineering.html` است و اسکریپت‌های feature جداگانه را بارگذاری می‌کند:

| بخش | فایل | مسئولیت |
|---|---|---|
| Shell و ساختار | `autonomous-engineering.html` | فرم‌ها، کنترل‌ها، وضعیت، سوابق، progress و راهنمای راه‌اندازی |
| Transport و خطا | `autonomous-engineering-core.js` | درخواست HTTP، timeout، correlation ID، parse پاسخ و پیام عمومی |
| وضعیت و readiness | `autonomous-engineering-status.js` | snapshot وضعیت، gateهای dispatch، وضعیت run فعال و polling |
| کنترل‌ها | `autonomous-engineering-controls.js` | ذخیره تنظیمات، pull مدل، ارسال task، dispatch، pause/resume/cancel |
| سوابق اجرا | `autonomous-engineering-runs.js` | فهرست runها، گزارش/log، حذف run قدیمی و cleanup |
| پیشرفت | `autonomous-engineering-progress.js` | progress جاری، scorecard، نمودار تاریخی و رخدادها |
| Bootstrap | `autonomous-engineering.js` | اتصال moduleها، چرخهٔ refresh و lifecycle صفحه |

### کنترل‌های قابل مشاهده در HTML

- refresh وضعیت کلی؛
- انتخاب حالت `continuous/scheduled` و فاصلهٔ اجرا؛
- انتخاب مدل نصب‌شده یا ورود شناسهٔ مدل؛
- pull مدل با اقدام صریح کاربر؛
- ذخیره تنظیمات؛
- ارسال task دستی با سقف 2000 نویسه؛
- dispatch چرخه از صف؛
- pause، resume و cancel اضطراری؛
- refresh سوابق، مشاهدهٔ گزارش هر run، حذف run پایان‌یافته و cleanup؛
- refresh progress، انتخاب بازهٔ نمودار و مشاهدهٔ رخدادها.

وجود کنترل در HTML به‌تنهایی اثبات کارکرد نیست؛ هر کنترل باید در browser E2E، API و state transition متناظر پوشش داده شود.

## 3. قرارداد API مشاهده‌شده در main

Router با prefix `/api/autonomous-engineering` و محدودیت loopback ثبت شده است.

| Method | Endpoint | کاربرد |
|---|---|---|
| GET | `/status` | وضعیت اجزا، مدل‌ها، runها و durable state |
| POST | `/models/pull` | شروع دریافت مدل پس از درخواست UI |
| GET | `/models/pull/{job_id}` | وضعیت job دریافت مدل |
| GET | `/progress` | progress جاری/تاریخی |
| POST | `/settings` | ذخیره مدل، فاصله و execution mode |
| POST | `/dispatch` | ارسال task دستی یا شروع چرخه |
| POST | `/pause` | توقف چرخه‌های بعدی |
| POST | `/resume` | ادامه از وضعیت پایدار |
| POST | `/cancel` | لغو run مشخص/فعال |
| GET | `/runs` | تاریخچهٔ اجرا |
| GET | `/runs/{run_id}/logs` | گزارش و diagnostic اجرا |
| DELETE | `/runs/{run_id}` | حذف run پایان‌یافته از فهرست/دادهٔ مجاز |
| POST | `/cleanup` | پاک‌سازی telemetry موقت با lockهای حفاظتی |

## 4. گردش‌کار UI تا state

| عملیات کاربر | مسیر | مالک/مرجع حقیقت | invariant لازم |
|---|---|---|---|
| مشاهده وضعیت | UI → GET status | API و منابع واقعی Ollama/GitHub/Broker | UI فقط projection است؛ شکست یک integration نباید برنامهٔ اصلی را از کار بیندازد |
| ذخیره تنظیمات | UI → POST settings → GitHub repository variables | API محلی و GitHub settings | مدل دقیق باید نصب و context/tools آن تأیید شود؛ ذخیره ناقص یا انتخاب خودکار ممنوع |
| دریافت مدل | UI → POST pull → GET pull job | Ollama محلی و pull job | فقط با اقدام صریح؛ مدل‌های موجود نباید حذف/جایگزین شوند |
| ارسال task | UI → POST dispatch → workflow_dispatch | admission lock، durable state و GitHub Actions | duplicate dispatch، نتیجهٔ مبهم و concurrent tab باید fail-closed مدیریت شوند |
| اجرای task | workflow → Runner → Codex/Ollama | workflow/task owner و lease/fencing | worker stale یا retry نباید state را overwrite کند |
| checkpoint/progress | controller/watcher → durable store → GET progress | Broker/checkpoint store | atomic write، owner معتبر و recovery پس از crash |
| PR handoff | Codex → local tests → GitHub checks → PR | task controller و GitHub | PR پس از exact-head CI باز بماند؛ merge خودکار ممنوع |
| pause/resume/cancel | UI → API → GitHub Actions | state پایدار و run ID دقیق | resume نباید task تکراری بسازد؛ cancel باید run صحیح را هدف بگیرد و checkpoint را نگه دارد |
| logs/history/cleanup | UI → API → GitHub/durable telemetry | run ID تأییدشده و cleanup locks | عدم نشت secret و عدم حذف task/checkpoint فعال |

## 5. پوشش Browser E2E موجود

در `tests/test_browser_e2e.py` در main فعلی 21 تست با نام Autonomous Engineering شناسایی شد. حوزه‌های پوشش شامل render/status، task dispatch، model picker، accepted-run lock، cancel، logs/delete/cleanup، diagnostics، progress ordering، run history ordering، pending-work blockers، pause/resume، RTL/mobile/keyboard، module fault isolation، structured error/correlation، model pull consent/failure، polling/backoff/visibility، serialization refreshهای پس از mutation، جلوگیری از duplicate dispatch در دو tab، contrast و timeout سفارشی است.

workflow `browser-e2e.yml` کل تست‌های علامت‌گذاری‌شده با `browser_e2e` را روی Chromium و برنامهٔ محلی ایزوله اجرا می‌کند و artifact شواهد مرورگر را بارگذاری می‌کند. این تست‌ها برای regression ضروری‌اند، اما جایگزین تست با Ollama واقعی، Runner واقعی، Codex واقعی، GitHub credential واقعی و state broker ویندوز روی میزبان مالک نیستند.

## 6. مدل مهندسی و تنظیمات Ollama

مالک پروژه تأیید کرده است که `gpt-oss:20b` مدل استفاده‌شده برای مهندسی است. این نام باید در اسناد نقش مهندسی داشته باشد؛ مقدار پیش‌فرض `learning_ollama_model` در `app/config.py` نباید به‌اشتباه به‌عنوان مسیر واقعی انتخاب مدل مهندسی تفسیر شود.

UI مهندسی مدل انتخابی را از وضعیت تنظیمات GitHub می‌خواند و ذخیره تنظیمات از مسیر API به repository Actions variables می‌رسد. موجودی مدل‌های نصب‌شده از Ollama محلی دریافت می‌شود. در این اتصال، مقدار فعلی repository variable و فهرست واقعی میزبان مالک قابل خواندن نبود؛ بنابراین هنوز تأیید نشده که مقدار فعال UI دقیقاً `gpt-oss:20b` است. مدل‌های دیگر نصب‌شده نیز تا دریافت فهرست مالک کامل تلقی نمی‌شوند. هیچ pull، تغییر مدل یا route تغییر خودکاری نباید انجام شود.

## 7. شکاف‌ها و گیت‌های باز

1. **زنجیره PRها و مبنای main:** #863/#866 روی base قدیمی‌اند و #869/#870/#872 stacked هستند؛ ترتیب انتقال و رفع conflict هنوز نیازمند تحلیل است.
2. **پذیرش میزبان واقعی:** دسترسی به UI واقعی، سرویس Ollama، مدل `gpt-oss:20b`، Codex CLI، Runner service، GitHub CLI و Broker روی سیستم مالک در این بررسی وجود نداشت.
3. **End-to-end واقعی:** browser CI روی محیط ایزوله و عمدتاً deterministic است؛ task کنترل‌شدهٔ واقعی باید جداگانه روی میزبان اجرا و از UI تا PR و exact-SHA CI مشاهده شود.
4. **Recovery واقعی:** crash/restart در target Windows، حفظ task/checkpoint، process ownership و ACLها باید با اجرای واقعی و شواهد قابل تکرار تأیید شوند.
5. **مدل و قابلیت tools:** باید از UI مدل محلی واقعی انتخاب شود و context metadata و tool calling واقعی پیش از dispatch تأیید شوند.
6. **Fault isolation:** readiness جزءبه‌جزء در کارت‌ها دیده می‌شود؛ باید با fault injection ثابت شود خاموشی Ollama/GitHub/Runner فقط همان capability را محدود می‌کند و UI/core/storage/صفحات مستقل فعال می‌مانند.
7. **Accessibility و UX:** تست خودکار RTL/mobile/keyboard/contrast موجود است؛ بازبینی دستی keyboard/screen-reader و استفاده‌پذیری روی میزبان مالک باز است.

## 8. ترتیب اجرای بعدی

- ابتدا زنجیرهٔ PRهای موجود را بدون حذف branch یا تغییر state زنده تحلیل کن و dependency graph و diffهای متداخل را ثبت کن.
- تغییرات #863/#869 و #866/#870/#872 را بر اساس قابلیت و کمترین blast radius به یک برنامهٔ انتقال قابل review تبدیل کن؛ هیچ تغییر سالمی را صرفاً به‌خاطر conflict حذف نکن.
- Gateهای state ownership، lock، lease/fencing و recovery را قبل از گسترش UI ببند.
- سپس UI/API contract را با browser E2E و fault injection کامل کن.
- پس از CI exact-head، روی میزبان مالک یک task غیرمخرب با مدل مهندسی `gpt-oss:20b` از خود UI اجرا و شواهد dispatch، progress، logs، PR، checks و recovery را ثبت کن.
- PRها Draft بمانند تا گیت‌های خودکار و پذیرش لازم تکمیل شوند. سیاست مخزن merge خودکار را ممنوع کرده است؛ merge دستی مالک و post-merge verification باید جدا بماند.

## 9. محدودیت ادعا

این snapshot فقط ثابت می‌کند کد، UI، API، تست‌ها و وضعیت GitHub در SHAهای فوق چه وضعی دارند. این snapshot اثبات نمی‌کند که برنامه روی رایانهٔ مالک اکنون نصب و آماده است یا یک task واقعی از مرورگر تا PR موفق شده است.
