# گزارش یکپارچه‌سازی و ممیزی معماری مخزن

> تاریخ بررسی: 2026-10-31
> شاخهٔ مبنا: `main`  
> commit مبنا: `7a2cf05a7b3485e3b157e41860783fda2e76a846` (main پس از PR #627). آخرین commit `main` یعنی `7a2cf05a7b3485e3b157e41860783fda2e76a846` نیز post-merge CI کامل و موفق دارد: 1737 passed/5 skipped در 176.88 ثانیه، Windows storage، dashboard smoke و dependency audit همگی موفق.
> هدف این سند: تعیین مرزهای ادغام، منابع مرجع مستندات و مواردی که پیش از ادعای آمادگی نهایی باید آزموده شوند.

## 1. نتیجهٔ اجرایی

مخزن در commit مبنا از قبل به‌صورت ماژولار سازمان‌دهی شده است؛ `app/main.py` نقطهٔ ترکیب FastAPI، middlewareها، lifecycle و بخشی از endpointهای اصلی است و routerها و زیرسامانه‌های مستقل در فایل‌های جدا قرار دارند. انتقال تمام کدها به یک فایل `main.py` ادغام استاندارد محسوب نمی‌شود: این کار coupling را بالا می‌برد، تست واحد را دشوارتر می‌کند و خطر regression را افزایش می‌دهد. بنابراین ساختار ماژولار حفظ می‌شود و ادغام در `main` باید از طریق تغییرات کوچک، قابل بازبینی و CI-پذیرفته انجام شود.

این گزارش به‌خودی‌خود ادعای اجرای محلی کل مجموعهٔ تست یا ممیزی خط‌به‌خط تمام 400 فایل نیست. برای چنین ادعایی باید checkout کامل، اجرای تست‌ها و ارزیابی عملی روی میزبان هدف در دسترس باشد.

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
- محدودیت‌های انسانی/عملیاتی و بازبینی امنیتی مستقل همچنان خارج از شواهد CI هستند. claim-review cost/budget integration در PR #584 و heartbeat retry با deadline محدود در PR #585 تکمیل و ادغام شده‌اند؛ CI پس از ادغام PR #585 روی commit `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94` موفق است.

- PR #584: claim review اختیاری از نظر انتخاب مدل، برآورد prompt/token و رزرو durable بودجه با مسیر تولید هم‌راستا شد؛ ادغام `20e30eeb77bf04e2169ced9db32ba1cff036c3d6` و post-merge CI موفق.
- PR #585: retry خطای موقت SQLite در heartbeat با deadline مبتنی بر monotonic clock و دو regression test اضافه شد؛ ادغام `b7507c0f13f9195265d4d110e84f7bc4a2a4cd94`. CI روی head و پس از ادغام کامل موفق شد؛ post-merge tests 1501 passed و 5 skipped گزارش کردند.
- تکرار شمارهٔ ADRها در ممیزی مستندات شناسایی و در همین PR اصلاح شد: تصمیم‌های قدیمی‌تر تاریخ 2026-10-15 شماره‌های `ADR-001` تا `ADR-004` را نگه داشتند؛ تصمیم‌های بعدی به‌ترتیب تاریخ به `ADR-005` تا `ADR-009` تغییر شماره یافتند. ارجاع متقابل lease مرورگر و ارجاع PR #531 نیز به `ADR-007` به‌روزرسانی شد. لینک‌های Markdown در CI باید این تغییر مسیر فایل‌ها را تأیید کنند.


## 10. بازبینی و شواهد جدید — 2026-10-30

- PR [#587](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/587), merge `eeab2cbc8e6daecde1a9407b9e5025643f8db59e`: طبقه‌بندی خطای heartbeat دقیق شد؛ فقط SQLite `BUSY`/`LOCKED` retry می‌شود و خطاهای دائمی storage فوراً تمدید lease را متوقف می‌کنند. تست‌های رگرسیون classification و stop-immediately اضافه و ADR-003 همگام شد.
- PR [#588](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/588), merge `e08b580670999926a5350e3d00bcb48b208492eb`: دو مسیر خروجی HTTP(S) (بازبین AI خارجی و بررسی citationهای AI-learning) به IP عمومی اعتبارسنجی‌شده pin شدند؛ hostname برای TLS حفظ، proxy و redirect غیرفعال، پورت‌های مجاز محدود و پاسخ بازبین پیش از JSON parsing به 64 KiB محدود شد. وابستگی runtime غیرضروری `httpx` حذف شد و تست‌های شبکه و سند `docs/security/EXTERNAL_REVIEWER_NETWORK_BOUNDARY.md` افزوده شدند.
- CI دقیق PR #588: tests [#41058860339](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41058860339) و Windows storage regressions، browser E2E [#41058860056](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41058860056)، dependency audit [#41058860070](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41058860070) موفق شدند؛ Sandbox integration نیز در run تست‌ها موفق بود.
- CI پس از ادغام روی `main` در commit `019e5589da3d9f13a6fe47f177ee5a8d8defb213`: tests [#41063660024](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063660024) با 1537 passed و 5 skipped؛ Windows storage regressions، browser E2E [#41063660116](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063660116) و dependency audit [#41063659957](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063659957) همگی موفق شدند. مراحل generated-artifact، Markdown link، import-cycle، retrieval benchmark و claim-to-test benchmark (10 مورد) نیز موفق بودند.
- این CI شواهد خودکار repository-level است؛ benchmark retrieval از fixture مصنوعی و fallback واژگانی استفاده کرد و خود log تصریح می‌کند که کیفیت عمومی retrieval را اثبات نمی‌کند. benchmark پوشش ادعا نیز ساختاری است و صحت معنایی ادعا یا کفایت آزمون را ثابت نمی‌کند. live-model، human adjudication، target-host، restore/rollback و independent security review همچنان دروازه‌های جداگانه‌اند.

## 11. تکمیل بازبینی امنیت و UI — 2026-10-30

- PR [#589](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/589), merge `b3040e5122cad109a608403d331e3ad8e3312285`: درخواست‌های GitHub در مسیر self-improvement بدون proxy محیطی و بدون redirect انجام می‌شوند؛ تست no-network و سند مرز bearer token اضافه شدند. Tests/Windows، browser E2E، dependency audit و Sandbox integration روی head دقیق موفق شدند.
- PR [#590](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/590), merge `3cefdf5928eeae6183f3cb317381ec9b1b43ab6a`: نصب‌کنندهٔ Ollama نسخهٔ رسمی، origin دارایی، اندازه و SHA-256 را کنترل می‌کند و cache را پیش از reuse دوباره hash می‌کند. تست‌های digest، partial download و حفظ installer معتبر قبلی افزوده شدند؛ tests، browser E2E و dependency audit موفق شدند. اجرای زندهٔ installer انجام نشده است؛ محدودیت payload مرحلهٔ دوم Linux در سند امنیتی ثبت شده است.
- PR [#591](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/591), merge `caf3f49a0ce5f411351195e412d7a3cd6a56e4a9`: دو تست Chromium برای رضایت، refresh دستی، revoke، خطای CLI و بازیابی Kaggle/OCI اضافه شدند؛ هیچ CLI یا حساب واقعی فراخوانی نمی‌شود. Tests/Windows، browser E2E و dependency audit موفق شدند.
- PR [#592](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/592), merge `019e5589da3d9f13a6fe47f177ee5a8d8defb213`: snapshotهای quota به هویت executable یا scope کامل OCI محدود شدند؛ تغییر scope، snapshotهای قدیمی را در تراکنش حذف می‌کند و نتیجهٔ refresh قدیمی در صورت تغییر consent رد می‌شود. ADR-010 و regressionهای API شامل تغییر scope و race افزوده شدند؛ tests/Windows، browser E2E و dependency audit موفق شدند.
- PR [#593](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/593), merge `c5e8a7c338c35b0c9267bb563344b9973ac56153`: محدودیت textarea برنامه‌نویسی از تنظیم ذخیره‌شده خوانده و پس از ذخیرهٔ موفق به‌روز می‌شود؛ تست Chromium ارسال، polling، نتیجه و خطای queue را پوشش می‌دهد و صریحاً تأیید می‌کند که کد تولیدشده اجرا نشده است. Exact-head tests 1537 passed/5 skipped، Windows storage، browser E2E و dependency audit موفق شدند؛ post-merge CI روی `main` در commit `c5e8a7c338c35b0c9267bb563344b9973ac56153` نیز کامل موفق شد: tests [#41065147895](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147895)، browser E2E [#41065147794](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147794) و dependency audit [#41065147868](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41065147868).
- شمارش فعلی درخت `main` در snapshot بررسی: 400 فایل در کل، 285 فایل Python، 157 فایل آزمون Python، 71 سند زیر `docs/` و 5 workflow. `tests/test_browser_e2e.py` اکنون 41 تابع مستقل دارد. این شمارش‌ها snapshot هستند و با تغییرات بعدی تغییر می‌کنند.
- اولویت‌های باقی‌ماندهٔ سطح UI: runtime واقعی Docker/cancellation و failure recovery در مرورگر، تعاملات knowledge-source، و جریان‌های pause/resume مرور AI-learning. پذیرش دستی keyboard/screen-reader و rendered contrast نیز باز است.
- محدودیت شواهد: مرورگرها و تست‌های quota، APIها را mock/intercept می‌کنند؛ installer و ارائه‌دهندهٔ خارجی به‌صورت زنده اجرا نشده‌اند. CI روی Ubuntu/Windows میزبان‌شده، پذیرش target-host، load/soak، staging restore/rollback یا بازبینی امنیتی مستقل را اثبات نمی‌کند.
## 12. سخت‌سازی transport منابع دانش — 2026-10-30

- PR [#595](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/595), merge `dbfb8849ca4b2e243abeded724635ab47eb7d364`: مسیر `app/knowledge_base.py` دیگر از Proxy محیطی استفاده نمی‌کند؛ policy parsing از DNS جدا شد و `PinnedHTTPSHandler` تنها یک بار، درست پیش از اتصال، همهٔ پاسخ‌های DNS را بررسی و IP عمومی را pin می‌کند. طول URL به 2048 نویسه محدود شد و redirectها قبل از اتصال فقط policy-check می‌شوند؛ resolver مقصد واقعی را دوباره resolve نمی‌کند.
- همان PR این single-resolution contract را در `app/web_sources.py` نیز اعمال کرد؛ `ProxyHandler({})` و pinning موجود حفظ شدند، اما lookup تکراری بین `_open`، redirect handler و resolver حذف شد. TLS hostname verification، رد IPهای غیرعمومی، allowlistهای موجود و محدودیت اندازهٔ پاسخ حفظ شدند.
- تست regression `test_knowledge_fetch_disables_proxies_and_resolves_only_at_pinned_connect`، تست مشابه برای web-source transport، URLهای غیرمتنی/بیش‌ازحد بلند و تست‌های redirect/private-IP اضافه شدند. یک assertion اولیه در تست redirect به‌علت ثبت class به‌جای instance در `urllib.build_opener` شکست خورد؛ assertion با رفتار واقعی urllib اصلاح و CI مجدد روی head نهایی اجرا شد.
- CI روی head دقیق PR #595 `616b5693863e1559bc51efbfa4200a5690ed55cf`: tests [#41066764707](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41066764707) با 1543 passed و 5 skipped، Windows storage regressions، browser E2E [#41066764789](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41066764789) و dependency audit [#41066764847](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41066764847) موفق شدند. Post-merge CI روی `main` هنوز در حال اجراست.
- آزمون‌ها شبکهٔ بیرونی را فراخوانی نمی‌کنند؛ resolver و transport با fixture کنترل‌شده آزموده شده‌اند. بازبینی مستقل امنیت، تست واقعی DNS/Proxy در میزبان مقصد و ارزیابی egress در محیط عملیاتی همچنان باز هستند.

## 13. شواهد پس از ادغام PR #595 — 2026-10-30

- CI روی `main` در commit دقیق `dbfb8849ca4b2e243abeded724635ab47eb7d364`: tests [#41067227257](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41067227257) با 1543 passed و 5 skipped؛ Windows storage regressions، browser E2E [#41067227286](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41067227286) و dependency audit [#41067227304](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41067227304) موفق شدند.
- در همان test job، تولید artifactها، بررسی لینک‌های Markdown برای 73 فایل، کنترل import-cycle، benchmark بازیابی واژگانی و benchmark ساختاری claim-to-test با 10 مورد موفق شدند. گزارش benchmark بازیابی تصریح می‌کند که fixture مصنوعی کیفیت عمومی retrieval را اثبات نمی‌کند؛ benchmark پوشش نیز کفایت معنایی آزمون‌ها یا صحت ادعاها را ثابت نمی‌کند.
- این شواهد CI مخزن هستند. پذیرش روی میزبان مقصد، آزمون زندهٔ مدل/ارائه‌دهنده، تمرین staging restore/rollback و بازبینی مستقل امنیتی همچنان دروازه‌های جداگانه‌اند.

## 14. تکمیل جریان منابع دانش و آزمون کنترل سراسری AI-learning — 2026-10-30

- مبنای فعلی پس از ادغام PR #597: `main` در commit `54c8f55dd9e2f5e163c454dedf37cdd766429874`. status checkهای post-merge این SHA هنگام ثبت این گزارش در دسترس نبودند؛ موفقیت CI روی headهای PR را با CI پس از ادغام یکی نگیرید.
- PR #596 (`e057abf17766368fc21f70020d9b87760dec298a`) رابط کاربری دریافت منبع HTTPS، بازخوانی فهرست منابع و جست‌وجوی محلی را اضافه کرد. وضعیت ingest از خطای refresh فهرست جدا نگه داشته می‌شود؛ خروجی با `textContent` نمایش داده می‌شود. exact-head tests، browser E2E و dependency audit موفق شدند. E2E درخواست‌های API را intercept می‌کند و دریافت زندهٔ اینترنتی را نمی‌سنجد.
- PR #597 (`54c8f55dd9e2f5e163c454dedf37cdd766429874`) آزمون Chromium کنترل سراسری pause/resume را اضافه کرد؛ خطای 503 در اولین resume باید دکمه را قابل استفاده نگه دارد و درخواست بعدی باید مکث را بردارد. exact-head tests، Windows storage regressions، browser E2E و dependency audit موفق شدند.
- محدودیت پوشش PR #597: فهرست دوره‌ها در fixture خالی است و payload هر دو درخواست resume برابر `{"course_id": null}` است؛ بنابراین مسیر resume دورهٔ انتخاب‌شده آزموده نشده است. این مورد به‌عنوان شکاف آزمون باقی می‌ماند، نه نقص اثبات‌شده در production.
- تغییرات فوق معماری و رفتار backend صف را تغییر نمی‌دهند. پذیرش روی میزبان مقصد، تعامل واقعی با provider، آزمون crash/multi-process و بازبینی مستقل امنیتی همچنان جداگانه لازم‌اند.


## 15. پوشش resume دورهٔ انتخاب‌شده در AI-learning — 2026-10-30

- PR [#599](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/599) تست مرورگر واقعی Chromium را برای resume یک دورهٔ مشخص اضافه کرد. fixture پاسخ فهرست دوره‌ها را با دورهٔ شناسهٔ 42 برمی‌گرداند، صفحهٔ جزئیات و runها را فراهم می‌کند و payload درخواست `POST /api/ai-learning/resume` را ثبت می‌کند؛ assertion نهایی الزام می‌کند payload برابر `{"course_id": 42}` باشد. تست قبلی کنترل سراسری با `course_id: null` حفظ شد.
- PR روی head دقیق `23832ee9c2cac7ea78412d58f27f2cae5ac40b5d` بررسی شد و سه workflow موفق شدند: tests [#41076114713](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076114713) (شامل Windows storage regressions)، browser E2E [#41076115543](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076115543)، dependency audit [#41076114829](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41076114829).
- ادغام PR #599 در `main`: `cd620a624e997fc52fd510c0fdfedc2280af02b9`. هیچ کد runtime تغییر نکرد. تست APIها را intercept می‌کند و فقط قرارداد UI-to-API را اثبات می‌کند، نه اجرای زندهٔ provider یا بازیابی عملیاتی.
- اجرای مستقل post-merge CI روی SHA ادغام‌شده در این بازبینی قابل تأیید نبود؛ آن را موفق فرض نکنید. پذیرش target-host، crash/multi-process، live-model و بازبینی مستقل امنیت همچنان دروازه‌های جداگانه‌اند.


## 16. بازیابی زمان‌بندی‌شده و cooldown سراسری AI-learning — PR #602

- PR [#602](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/602) با commit `e02b5941345512353189c25678832f129324f7ff` در `main` ادغام شد. migration شماره 6 ستون‌های شمارنده و زمان retry را به runs و زمان/علت cooldown پایدار را به کنترل سراسری اضافه می‌کند. index ترکیبی برای یافتن اجرای queued و موعددار ایجاد شده است.
- retryها فقط برای خطاهای transient مشخص و حداکثر دو بار زمان‌بندی می‌شوند: شبکه/صفحه/مرورگر 60 و 300 ثانیه، timeout پاسخ 120 و 600 ثانیه، و rate limit ارائه‌دهنده 300 و 900 ثانیه. پاسخ ناقص ذخیره می‌شود. cooldown سراسری از زنجیره‌ای‌شدن درخواست‌های queued هنگام اختلال provider جلوگیری می‌کند و maintenance cycle موجود اجرای due retry را انجام می‌دهد.
- ورود، CAPTCHA/تأیید انسانی، وابستگی‌های نصب‌نشده، بسته‌شدن غیرقابل‌بازیابی مرورگر و خطاهای محتوا/ذخیره‌سازی در مسیر `needs_user_action` می‌مانند. کاربر می‌تواند پس از رفع علت بازیابی دستی انجام دهد؛ cancellation و pause به retry خودکار تبدیل نمی‌شوند.
- exact-head CI روی `9b629f2a5bef699261fbc0097f411e011b656366`: tests [#41087746251](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746251) (1556 passed، 5 skipped و Windows storage regressions)، browser E2E [#41087746167](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746167)، dependency audit [#41087746175](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41087746175) — همگی موفق. 73 فایل Markdown از نظر لینک، 112 ماژول از نظر import-cycle و artifactهای تولیدشده نیز بررسی شدند.
- محدودیت: E2E مرورگر و تست‌های retry با fixture/mock انجام می‌شوند و اجرای زندهٔ ChatGPT یا دسترس‌پذیری واقعی provider را ثابت نمی‌کنند. benchmark lexical با 16 query روی fixture و claim-test با 10 case فقط regressionهای تعریف‌شده را می‌سنجند. اجرای مستقل post-merge CI روی SHA ادغام‌شده در شواهد این بازبینی تأیید نشد. صف worker هنوز single-process است و lease توزیع‌شده یا restore دقیق session مرورگر وجود ندارد.


## 17. API regression برای شواهد ابری منقضی‌شده — PR #611

- PR [#611](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/611) added an API regression for a previously approved/available cloud candidate whose last observation is 31 days old. It independently exercises stale claim review and candidate review, expects HTTP 409 for both, and verifies the list API exposes stale freshness with availability reset to unknown. The change is test-only; production behavior and schema are unchanged. Exact PR-head CI on `ec693cb218b7f311c7ed82458d67ada0f58cdf4a` passed: tests [#41158610985](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610985) (including Windows storage regressions and repository quality checks), browser E2E [#41158611003](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158611003), and dependency audit [#41158610974](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41158610974). Squash merge commit: `2fd885a583e0c6252aa6b77a23c5cff56bc87c31`. The available GitHub evidence did not confirm post-merge workflows on the squash SHA, so post-merge CI is not claimed. Automated Codex review could not complete because the repository review quota was exhausted; no completed independent code review is claimed.
- ممیزی منطق موجود در `app/cloud_resources.py` نشان داد هر دو مسیر `review_candidate` و `review_claim` پیش از تغییر وضعیت، stale بودن candidate را بررسی می‌کنند؛ مسیر list نیز وضعیت freshness و reset availability را منعکس می‌کند. PR #611 این قراردادهای موجود را در سطح HTTP پوشش داد و منطق تولید را تغییر نداد.
- محدودیت باقیمانده: اجرای CI روی head دقیق PR تأیید شده است، اما اجرای مستقل workflowهای post-merge روی commit ادغام‌شده از شواهد قابل‌دسترسی این بازبینی قابل تأیید نیست. بازبینی خودکار Codex نیز به علت سقف مصرف کامل نشد.


## 18. قرارداد اعتبارسنجی عددی در مرز HTTP — PR #614 و PR #615

- PR [#614](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/614) پس از عبور exact-head CI در commit 8e461eaf09952565a56d4e96a11931d091a7002d ادغام شد؛ commit ادغام 9feecabd8a82bfa37e2317ee53491a9a07c28568 است. این مرحله Settings، task و research API را در برابر تبدیل ضمنی مقدارهای عددی نامعتبر سخت‌گیرانه کرد و تست‌های API برای عدم mutation در درخواست نامعتبر افزود.
- PR [#615](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/615) ادامهٔ همان قرارداد را در مدل‌های body مربوط به AI-learning، تجربه و اجرای کنترل‌شده، جست‌وجوی منبع، همگام‌سازی منبع، پیشنهاد یال گراف دانش، نسخهٔ Oracle و retention نسخه‌های پشتیبان اعمال کرد. فیلدهای شناسه و شمارنده از StrictInt و آرایه‌های شناسه از list[StrictInt] استفاده می‌کنند.
- تصمیم سازگاری: فقط JSON body modelها تغییر می‌کنند؛ query/path parameterها و داده‌های ذخیره‌شده تغییر نمی‌کنند. فیلد امتیاز یادگیری که مقدار اعشاری معتبر می‌پذیرد همچنان اعشاری باقی می‌ماند. هیچ migration یا تغییر schema در این مرحله وجود ندارد.
- فیلدهای rating با Literal عددی نیز به‌صورت صریح بررسی نوع می‌شوند؛ در Pydantic، مقدار boolean و float می‌تواند با Literal عددی برابر تلقی شود. بنابراین فقط integer واقعی با مقدار مجاز -1 یا 1 پذیرفته می‌شود.
- تست‌های tests/test_api_strict_integer_models.py برای هر فیلد پوشش‌داده‌شده، مقدارهای boolean، اعشاری و رشتهٔ عددی را رد می‌کنند و integer معتبر را می‌پذیرند؛ برای آرایه‌ها، عناصر نامعتبر نیز جداگانه آزموده می‌شوند. تست‌های API قبلی PR #614 عدم تغییر state را برای چند مسیر حساس اثبات می‌کنند.
- اعتبارسنجی exact-head روی commit `6d9b8eed5d4cfa50e98a9a048a406b4fbb8822f8` موفق شد: tests [#41161075045](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075045) با 1719 passed و 5 skipped، Windows storage regressions، artifact validation، Markdown links، import-cycle، retrieval benchmark و claim-to-test benchmark (10/10)؛ browser E2E [#41161075132](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075132) و dependency audit [#41161075015](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41161075015) نیز موفق شدند. PR #615 با commit `8ea37617c631c89e6b0a36621061315ee38d5a19` در main ادغام شد. اجرای مستقل post-merge روی SHA ادغام‌شده هنوز تأیید نشده است.
- محدودیت: این تست‌ها قرارداد parsing مدل را اثبات می‌کنند؛ جایگزین تست یکپارچهٔ هر workflow یا پذیرش روی میزبان مقصد نیستند. تغییرات این مرحله نباید به‌عنوان تکمیل کل پروژه یا عبور از دروازه‌های live-model، امنیت مستقل، بازیابی/soak و staging تعبیر شوند.

## 19. تشخیص privacy-safe برای شکست بازیابی منبع — PR #617

- PR [#617](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/617) مسیر صفر منبع را قابل‌تشخیص‌تر کرد: \`discover_sources\` شمارنده‌های candidate، تلاش fetch، موفقیت fetch و تعداد خطاهای دسته‌بندی‌شده را برمی‌گرداند؛ diagnostics موتورهای جست‌وجو نیز به audit پژوهش منتقل می‌شوند.
- \`chat_audit\` فقط providerهای شناخته‌شده، variant و endpoint مجاز، شمارنده‌های غیرمنفی و چند error category مشخص را نگه می‌دارد. query، URL ناموفق، متن صفحه و جزئیات خام exception ذخیره نمی‌شوند. گزارش \`run_answer_quality_eval.py\` این diagnostics را برای هر case در دسترس قرار می‌دهد.
- تست‌های جدید مسیر fetch ناموفق/موفق، ثبت diagnostics در حالت صفر منبع، حفظ diagnostics هنگام خطای provider و حذف داده‌های حساس را پوشش می‌دهند.
- exact-head CI روی \`8c6aa6d68bafc95ee2509b59b9db786271e43c70\` موفق شد: tests [#41162151299](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151299) با 1722 passed و 5 skipped، Windows storage regressions، browser E2E [#41162151340](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151340)، dependency audit [#41162151309](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41162151309)، artifact/link/import checks و benchmarkهای retrieval و claim-to-test.
- PR #617 با squash commit \`e6c40e2362d1082fb9631009a693db61cfc7fffc\` ادغام شد. اجرای مستقل post-merge روی این SHA در شواهد قابل‌دسترسی تأیید نشده است.
- محدودیت: این تغییر منشأ خطای صفر منبع را در گزارش مشخص می‌کند؛ provider availability، صحت factual پاسخ، اجرای زندهٔ Ollama و human adjudication را اثبات نمی‌کند.

## 2026-10-31 — Linux Ollama second-stage payload integrity (PR #620)

- PR [#620](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/620) closes the known Linux installer gap: the actual Linux archive is selected from the same official GitHub release manifest as install.sh, and downloaded with the exact release URL, SHA-256 digest, and declared size.
- The upstream download_and_extract implementation is patched to call the existing bounded _download_once path instead of its unverified second-stage curl download. Redirect-host restrictions, response-size checks, and digest verification are reused rather than duplicated.
- Unknown upstream installer structure and unsupported architectures fail closed. The verified archive is decompressed to a temporary tar file before extraction so the official /bin/sh installer remains POSIX-compatible and decompression errors cannot be hidden by a pipeline.
- Regression tests cover supported release asset discovery, version/URL/digest/size validation, rejection of untrusted or malformed assets, upstream script drift, and POSIX sh -n syntax validation.
- PR #620 was squash-merged into `main` as `a85c7e85b93c322d6a093ee7ba0319b30a6f2147`. Exact-head CI passed on `5c1c1f36f5f38b43a2ec20e720548fde299a43f8`: tests [#41164300720](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300720) (1728 passed, 5 skipped; includes Windows storage regressions, artifact validation, 77-file Markdown link validation, import-cycle scan, retrieval benchmark, and claim-to-test benchmark 10/10), browser E2E [#41164300693](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300693), and dependency audit [#41164300658](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41164300658). Independent post-merge CI on the squash SHA is not confirmed. No live Linux installation was run; target-host installation remains an excluded local acceptance step.

## 2026-10-31 — Consistent HTTPS policy for research sources (PR #622)

- PR [#622](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/622) rejects HTTP search results before applying the bounded result limit, so an HTTP-only candidate cannot crowd out a usable HTTPS result and later be discarded by the chat source-payload policy.
- Document fetching independently rejects non-HTTPS URLs before network access. Redirects from HTTPS to HTTP are also rejected, preventing transport downgrade. Search normalization, source payloads, citations, and evidence records now share the same HTTPS requirement.
- Regression coverage checks HTTPS selection after an HTTP candidate, rejection before transport, and bounded discovery diagnostics.
- PR #622 با squash commit `c46e136bb00e4dd56627e98990b80d27f73b93e9` در `main` ادغام شد. Exact-head CI روی `5abb8ac82576601e02207810402f58922c0fda56` موفق شد (1735 passed/5 skipped) و post-merge tests [#41166674541](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41166674541)، Windows storage regressions، dashboard smoke [#41166674561](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41166674561) و dependency audit [#41166674550](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41166674550) نیز موفق شدند. این تغییر یک ناسازگاری قطعی قرارداد URL را می‌بندد، اما علت صفر بودن منابع در اجرای زندهٔ قبلی را به‌تنهایی اثبات نمی‌کند.

## 2026-10-31 — Claim audit, test synchronization, and local Host boundary

- PR [#623](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/623) persists bounded claim-review provenance in the privacy-preserving chat audit: prompt version/hash, model identifier and digest status, bounded provider usage counters, review summary, and source metadata/content hashes. Raw prompts, answers, claim text, free-form review reasons, and source excerpts are not persisted. Merged as `1e46eff5aeafd1a470a421df6dbea81629a89266`; exact-head and post-merge tests, Windows storage, browser smoke, and dependency audit passed.
- PR [#624](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/624) corrected stale AI-learning lease and claim-review documentation. It records the 90-second SQLite lease, 15-second heartbeat, fencing epoch, single-host scope, claim-review-v2, and the bounded audit contract. Exact-head tests (1730 passed/5 skipped) and all CI checks passed; post-merge workflows also passed on `acb821df03c8c24c7b18cc912f3a1881964fb863`.
- PR [#625](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/625) fixed a browser-test race: the self-improvement success message appears before the asynchronous proposal-list refresh completes, so the test now waits for the matching card before asserting. This was a test synchronization fix, not a production persistence failure. Merged as `04d325e244fc5b231d74ac7b700b571b4cf45283`; exact-head and post-merge CI passed.
- PR [#626](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/626) strengthened the local app boundary by requiring both a loopback peer and an exact local Host hostname, mitigating DNS-rebinding access through attacker-controlled Host values. It explicitly does not provide authentication or complete CSRF protection. Merged as `6fdd176e81aa408ff17cc1c01ba66ec198f42240`; post-merge tests [#41166685079](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41166685079) reported 1737 passed/5 skipped; dashboard smoke, Windows storage regressions, and dependency audit passed.
- PR [#627](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/627) corrected the stale Linux Ollama installer security document to record the verified second-stage archive introduced by PR #620, while retaining the unperformed live target-host installation as an open gate. Merged as `7a2cf05a7b3485e3b157e41860783fda2e76a846`; exact-head CI passed (1730 passed/5 skipped), and post-merge CI on current `main` passed (1737 passed/5 skipped in 176.88s), including Windows storage, dashboard smoke, and dependency audit.
- The current tree count and CI evidence are repository snapshots, not a claim that every line of all 400 files has been manually reviewed. Remaining acceptance gates include live-model/human adjudication, independent security review (Issue #52), target-host startup/crash/soak, staging restore/rollback, and the dedicated real-Docker acceptance path.
