# گزارش یکپارچه‌سازی و ممیزی معماری مخزن

> تاریخ بررسی: 2026-10-30
> شاخهٔ مبنا: `main`  
> commit مبنا: `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94` (main پس از ادغام PR #585؛ tests و browser E2E پس از ادغام در حال اجرا، dependency audit موفق).  
> هدف این سند: تعیین مرزهای ادغام، منابع مرجع مستندات و مواردی که پیش از ادعای آمادگی نهایی باید آزموده شوند.

## 1. نتیجهٔ اجرایی

مخزن در commit مبنا از قبل به‌صورت ماژولار سازمان‌دهی شده است؛ `app/main.py` نقطهٔ ترکیب FastAPI، middlewareها، lifecycle و بخشی از endpointهای اصلی است و routerها و زیرسامانه‌های مستقل در فایل‌های جدا قرار دارند. انتقال تمام کدها به یک فایل `main.py` ادغام استاندارد محسوب نمی‌شود: این کار coupling را بالا می‌برد، تست واحد را دشوارتر می‌کند و خطر regression را افزایش می‌دهد. بنابراین ساختار ماژولار حفظ می‌شود و ادغام در `main` باید از طریق تغییرات کوچک، قابل بازبینی و CI-پذیرفته انجام شود.

این گزارش به‌خودی‌خود ادعای اجرای محلی کل مجموعهٔ تست یا ممیزی خط‌به‌خط تمام 360 فایل نیست. برای چنین ادعایی باید checkout کامل، اجرای تست‌ها و ارزیابی عملی روی میزبان هدف در دسترس باشد.

## 1.1 تغییرات تأییدشده پس از بازبینی قبلی

در snapshot مبنا، تغییرات زیر به‌صورت مرحله‌ای به `main` رسیده‌اند:

- PR [#567](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/567) روی `main` ادغام شد: صف پایدار FIFO برای اجرای batchهای AI-learning، بازیابی restart، pause/resume بدون terminal-cancel، و جلوگیری تراکنشی از ثبت lesson در race بین pause و completion. دو regression test مشخص این raceها را پوشش می‌دهند. Merge commit: `0640c7f02f8f234b8f9b5075deda47c9fc2d3f2a`. پس از ادغام، test suite برابر 1458 passed و 5 skipped، Windows storage regressions، browser E2E و dependency audit همگی روی همین SHA موفق شدند.

- PR [#483](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/483): migration 24 و دفتر idempotent ثبت اجرای کانال‌های بازبینی نامزد؛ نتیجهٔ ثبت‌شده به‌تنهایی موجب ارتقای اعتماد نمی‌شود.
- PR [#484](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/484): بررسی لینک‌های Markdown محلی در CI.
- PR [#485](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/485): تبدیل بررسی چرخه‌های import زمان بارگذاری به گیت شکست CI.
- PR [#486](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/486): pin کردن اتصال HTTP(S) به IP عمومی اعتبارسنجی‌شده، حفظ نام میزبان TLS و آزمون رگرسیون DNS/redirect.
- PR [#488](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/488): ارزیابی معنایی assessment فقط با مدل نصب‌شده‌ای که digest آن از مدل‌های تولید متمایز است؛ نمرهٔ قطعی تغییر نمی‌کند.
- PR [#492](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/492): DAG پیش‌نیازهای چرخه‌-ایمن؛ مسیرهای قدیمی بدون سیاست صریح، رفتار قبلی موازی را حفظ می‌کنند.
- PR [#493](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/493): pipeline ممیزی کانال‌های بازبینی، فهرست oracle، promptهای نسخه‌دار، بودجهٔ پایدار در سطح job/روز، معیارهای عملیاتی و مهاجرت‌های 28–31.
- PR [#497](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/497): بازبینی factual با oracle مستقل، مدیریت چرخهٔ عمر دانش، گراف روابط دانش، نقشهٔ قابلیت یادگیری، ثبت مصرف/هزینهٔ مدل، و یادگیری مرورگری AI-to-AI از طریق Chrome محلی؛ خروجی مرور یا مدل به‌تنهایی باعث ارتقای خودکار اعتماد نمی‌شود.
- PR [#499](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/499): شناسه‌گذاری ادعاهای نامزد، کنترل ساختاری پوشش هر ادعا با آزمون‌های صریح و benchmark نسخه‌دار در CI؛ این گیت کفایت معنایی آزمون یا حقیقت ادعا را تضمین نمی‌کند.

آزمون‌های PR برای هر تغییر بالا موفق بودند. اجرای CI روی commit نهایی `main` نیز باید جداگانه از صفحهٔ Actions تأیید شود؛ این گزارش ادعای اجرای محلی checkout یا تست زندهٔ میزبان را ندارد.

PRهای قدیمی #462، #495، #459، #370، #369 و تلاش ادغام قدیمی #498 پس از ممیزی با وضعیت superseded بسته شدند؛ قابلیت‌های منحصربه‌فرد معتبر از #462/#495 در #497 و گیت پوشش ادعا از #370 در #499 به‌صورت هدفمند منتقل شدند. سایر بخش‌های آن شاخه‌ها به دلیل هم‌پوشانی با پیاده‌سازی‌های جدیدتر، ناسازگاری با ساختار فعلی یا مستندات/رابط قدیمی به‌صورت یکجا ادغام نشدند. هیچ PR بازی در snapshot مبنا باقی نمانده بود.


## 1.2 اصلاحات تأییدشده پس از snapshot قبلی

- PR [#515](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/515), merge `07c1416f60ae7c69dcbf29812a7f397d6d17e8b3`: fault injection برای شکست موقت ذخیرهٔ نتیجهٔ وظیفه، retry و جلوگیری از انتشار نتیجه پیش از ثبت پایدار.
- PR [#516](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/516), merge `af9bc22a2a421900e0ed933a35f93813834bf5de`: بررسی پیش‌نیاز پیش از تغییر وضعیت و حفظ درس موجود هنگام شکست کنترل‌شدهٔ تولید مجدد.
- PR [#517](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/517), merge `53e83bc51bf8f734bad782e0722d4e221f04cb67`: جلوگیری از اجرای handler بدون heartbeat و بازیابی retryable در شکست شروع heartbeat.
- PR [#518](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/518), merge `7622b7bb8d6985b4b5706c6acd63a5f5973396e4`: گیت تکمیل سرفصل بر اساس منشأ ارزیابی و آستانهٔ نمره، با اعتبارسنجی ورودی.
- PR [#519](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/519), merge `66947d2aa534cc4d17c95ef6d977a701109a2f7a`: migration 39 برای منشأ نمرهٔ assessment و اتصال نتیجهٔ تمرین sandbox به سیاست تکمیل عمومی.
- PR [#521](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/521), merge `b1b71c20699175dcf4791959a3b295e70fca97f3`: کلید idempotency مبتنی بر شناسهٔ task برای ثبت ارزیابی عملی؛ replay نتیجهٔ قبلی را برمی‌گرداند و از درج attempt تکراری جلوگیری می‌کند. test، dependency audit، browser E2E و sandbox integration CI روی head دقیق موفق شدند.
- PR [#522](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/522), merge `cca0bae1571eac2bed7f80717156b126046ec32b`: بازیابی یک‌باره و محدود پس از قطع‌شدن Chrome context، با توقف و نیاز به بازیابی دستی در صورت قطع‌شدن مجدد. test، dependency audit و browser E2E روی head دقیق موفق شدند.
- PR [#524](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/524), merge `47a7a9cc77e59de516263118e3fcddcab37ddfed`: بازیابی سراسری runهای قطع‌شده و انتخاب FIFO از SQLite، بدون وابستگی به صفحه‌بندی داشبورد؛ بازیابی با شروع worker محلی سریالی شده است. test، dependency audit، browser E2E و Windows storage regressions روی head دقیق موفق شدند.
- PR [#526](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/526), merge `d928404224771c343e4ed2495d378bc936632cd5`: بازیابی خودکار دوره‌های قطع‌شده در مسیر قدیمی browser learning، حفظ درس‌های تکمیل‌شده و dispatch صف‌شدهٔ تک‌نمونه‌ای؛ پس از شکست، برای جلوگیری از retry storm صف متوقف می‌ماند. test، dependency audit، browser E2E و Windows storage regressions روی head دقیق موفق شدند.
- هر پنج PR روی head دقیق خود در CI آزمون‌ها، audit وابستگی‌ها و browser E2E را گذراندند؛ آزمون‌های storage ویندوز نیز در workflow مربوط موفق شدند. این شواهد خودکار جایگزین پذیرش میزبان هدف، اجرای زنده یا بررسی امنیتی مستقل نیستند.

## 2. نمای مخزن در commit مبنا

- 383 ورودی در درخت Git، شامل 360 فایل.
- `app/`: 126 فایل؛ کد برنامه، routerها، منطق کسب‌وکار، UI و زیرسامانهٔ یادگیری.
- `tests/`: 148 فایل؛ آزمون‌های واحد، رگرسیون و یکپارچه‌سازی.
- `docs/`: 54 فایل؛ معماری، عملیات، امنیت، یادگیری و نیازمندی‌ها.
- `.github/workflows/`: پنج workflow برای آزمون‌ها، audit وابستگی، browser E2E و sandbox.
- نقطهٔ ورود: `run.py`؛ برنامهٔ ASGI: `app.main:app`.

این شمارش مربوط به snapshot یادشده است و با تغییرات بعدی می‌تواند تغییر کند.

## 3. مرزهای معماری فعلی

- **Composition root و HTTP lifecycle:** `app/main.py`؛ ساخت FastAPI، نصب routerها، middlewareهای امنیتی، startup/shutdown و endpointهای عمومی.
- **وظایف پس‌زمینه:** `app/task_manager.py`, `app/task_runtime.py`, `app/task_worker.py`؛ صف پایدار، lease، checkpoint، worker و dispatch محدودشده.
- **زیرسامانهٔ یادگیری:** `app/learning.py`, `app/learning_tasks.py` و ماژول‌های ارزیابی/دانش/مرور؛ مستند مرجع `docs/LEARNING.md`.
- **یادگیری اختیاری از رابط AI:** `app/ai_learning/` با router، سرویس، ذخیره‌سازی، browser automation و feature flag مستقل.
- **مرزهای API:** routerهای جدا برای task، cloud resources، sandbox، experience، research، programming، self-improvement، factual verification و learning candidates.
- **ذخیره‌سازی و مهاجرت:** SQLite با schemaهای ثبت‌شده، ابزارهای تشخیصی و مسیر fail-closed برای schema ناشناخته.
- **رابط کاربری:** فایل‌های static و JavaScript در `app/static/` و رابط مستقل AI-learning در `app/ai_learning/static/`.

این مرزبندی دلیلی برای ادغام فیزیکی همهٔ ماژول‌ها در یک فایل نیست؛ قراردادها، وابستگی‌ها و آزمون‌های بین‌ماژولی باید به‌جای flatten کردن ساختار، تقویت شوند.

## 4. استانداردهای ایمنی و پایداری که باید حفظ شوند

1. API احراز هویت راه‌دور ندارد و باید فقط روی loopback در دسترس باشد؛ middleware فعلی اتصال غیر-loopback را رد می‌کند.
2. schema ناشناخته نباید خودکار مهاجرت یا تعمیر شود؛ حفظ داده و fail-closed بر startup ظاهراً موفق اولویت دارد.
3. task dispatch باید allowlist داشته باشد و handler ناشناخته اجرا نشود.
4. شکست زیرسامانه‌های اختیاری نباید مسیر اصلی چت را از دسترس خارج کند.
5. تغییرات self-improvement نباید بدون تأیید صریح به production ادغام یا deploy شوند.
6. خروجی مدل و URL به‌تنهایی منبع حقیقت یا مدرک صحت علمی نیست.
7. CI موفق به‌تنهایی اثبات‌کنندهٔ کیفیت factual، امنیت مستقل یا آمادگی production نیست.

## 5. سیاست ادغام در شاخهٔ main

- تغییرات باید در شاخهٔ جداگانه و Pull Request انجام شوند؛ ادغام مستقیم و بدون دروازهٔ آزمون توصیه نمی‌شود.
- پیش از حذف فایل یا تابع، باید تمام importها، routeها، ارجاعات UI، اسکریپت‌ها، مستندات و fixtureهای آزمون بررسی شوند.
- حذف فقط برای کد/سند تکراری یا بدون مصرفی مجاز است که نبود مصرف آن با جست‌وجو و آزمون تأیید شده باشد.
- تغییرات معماری بزرگ باید مرحله‌ای باشند: استخراج یک مسئولیت، افزودن/انتقال تست، اجرای کل suite، بررسی diff و سپس مرحلهٔ بعد.
- migration، backup/restore و shutdown/queue lifecycle باید با آزمون‌های regression و در صورت امکان fault injection پوشش داده شوند.

## 6. منابع مرجع مستندات

| موضوع | منبع مرجع |
|---|---|
| شروع سریع و نمای کلی | `README.md` |
| معماری و مرز ماژول‌ها | `docs/ARCHITECTURE.md` |
| نصب، توسعه و آزمون | `docs/DEVELOPMENT.md` |
| وضعیت پروژه و دروازه‌های پذیرش | `docs/PROJECT_STATUS.md` |
| منطق، محدودیت‌ها و نقشهٔ راه یادگیری | `docs/LEARNING.md` |
| مهندسی ماژول AI-learning | `docs/AI_LEARNING_ENGINEERING.md` |
| تست و فعال‌سازی AI-learning | `docs/AI_LEARNING_TESTING.md` |
| روش اعتبارسنجی دانش | `docs/AI_LEARNING_KNOWLEDGE_VALIDATION.md` |
| نیازمندی‌های هدف (نه ادعای پیاده‌سازی) | `docs/SYSTEM_REQUIREMENTS.md` |
| backup/restore و عملیات | `docs/DATABASE_BACKUP.md` و `docs/operations/` |
| مرز امنیت Ollama | `docs/security/OLLAMA_LOOPBACK_HARDENING.md` |

گزارش‌های تاریخی CI باید به‌عنوان شواهد زمان‌مند خوانده شوند، نه وضعیت فعلی. وضعیت واقعی باید از commit و workflow run مشخص در GitHub بررسی شود.

## 7. دروازه‌های لازم پیش از اعلام تکمیل نهایی

- اجرای کل `pytest` روی commit نهایی.
- بررسی موفقیت workflowهای Linux و Windows، به‌علاوهٔ browser E2E و sandbox integration در محیطی که پیش‌نیازهای آن فراهم است.
- بررسی خروجی `python scripts/generate_learning_artifacts.py --check` و `python scripts/check_import_cycles.py`.
- اجرای backup و restore آزمایشی روی staging و تأیید حفظ داده‌ها.
- fault-injection برای crash/retry/lease/cancel و بازیابی صف.
- آزمون زندهٔ Ollama و ارزیابی کیفیت پاسخ با مجموعهٔ نسخه‌دار و داوری انسانی.
- بازبینی امنیتی مستقل؛ به‌ویژه مرز loopback/Ollama و نبود احراز هویت راه‌دور.
- آزمون روی سیستم‌عامل و سخت‌افزار مقصد و نگهداری گزارش redacted.

## 8. محدودیت این تغییر مستنداتی

این سند نقشهٔ ادغام و منبع مرجع است؛ جایگزین اجرای آزمون‌ها، بازبینی کامل کد یا پذیرش عملیاتی نیست. در این تغییر، رفتار زمان اجرای برنامه عمداً تغییر نمی‌کند. هر تغییر کد باید جداگانه با تست و شواهد CI تأیید شود.


## 9. تکمیل ممیزی پس از snapshot قبلی — 2026-10-30

- PR #580: دفتر پذیرش و شواهد اجرای پروژه همگام شد؛ commit `0521424dd4882bbe61d69dc95321ed53238f5e2e`.
- PR #581: race حذف هم‌زمان کانتینرهای orphan با تأیید نبودن کانتینر پس از خطای حذف ایمن شد؛ commit `483343a2d9d67b6c8f7e5133497c8476440cfa5e`. CI پس از ادغام شامل real-Docker Sandbox integration و تست‌ها، browser E2E و dependency audit موفق بود.
- PR #582: انتخاب مدل هزینه/کیفیت به مسیر تولید درس متصل شد؛ commit `7ccba9de397f6de63c2535d2d2e955af245e7da1`. Post-merge tests 1494 passed و 5 skipped، Windows storage regressions، browser E2E و dependency audit موفق شدند.
- محدودیت‌های انسانی/عملیاتی و بازبینی امنیتی مستقل همچنان خارج از شواهد CI هستند. claim-review cost/budget integration در PR #584 تکمیل و ادغام شده است؛ heartbeat retry با deadline محدود نیز در PR #585 ادغام شد. وضعیت فعلی post-merge CI باید از runهای commit `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94` خوانده شود.

- PR #584: claim review اختیاری از نظر انتخاب مدل، برآورد prompt/token و رزرو durable بودجه با مسیر تولید هم‌راستا شد؛ ادغام `20e30eeb77bf04e2169ced9db32ba1cff036c3d6` و post-merge CI موفق.
- PR #585: retry خطای موقت SQLite در heartbeat با deadline مبتنی بر monotonic clock و دو regression test اضافه شد؛ ادغام `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94`. CI روی head PR موفق؛ پس از ادغام dependency audit موفق و tests/browser E2E هنگام snapshot در حال اجرا.
- موارد کشف‌شده در ممیزی مستندات: شماره‌های ADR در چند فایل تکرار شده‌اند (`ADR-002`, `ADR-003`, `ADR-004`). پیش از تغییر نام باید تمام لینک‌ها و ارجاعات متنی یک‌جا ممیزی و سپس با redirect/ارجاع سازگار اصلاح شوند؛ این مورد هنوز حل نشده است.
