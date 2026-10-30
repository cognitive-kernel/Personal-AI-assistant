# وضعیت یکپارچهٔ پروژه

> تاریخ بازبینی: 2026-10-30  
> آخرین commit کد بررسی‌شده: `019e5589da3d9f13a6fe47f177ee5a8d8defb213` در `main` (پس از PR #588). CI پس از ادغام روی همین SHA کامل موفق شد: tests [#41063660024](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063660024) با 1537 passed و 5 skipped، Windows storage regressions، browser E2E [#41063660116](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063660116) و dependency audit [#41063659957](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41063659957). بررسی artifactها، لینک‌های Markdown، چرخه‌های import، retrieval benchmark و benchmark پوشش ادعا (10 مورد) نیز موفق شدند. PR #588 علاوه بر تست‌های اصلی، Sandbox integration CI [#41058860339](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/41058860339) را نیز با موفقیت گذراند.  
> این سند مرجع وضعیت کلی پروژه و دروازه‌های عملیاتی است؛ مرجع واحد جزئیات یادگیری، محدودیت‌ها و نقشهٔ راه آن `docs/LEARNING.md` است. تیک فقط برای قابلیتی است که وجود آن در کد یا قرارداد فعلی مخزن شواهد دارد؛ تیک به‌تنهایی به معنی پذیرش عملیاتی روی دستگاه مقصد یا اثبات صحت علمی نیست. تست‌های محلی و تست‌های انسانی در فهرست کارهای باقی‌ماندهٔ این سند شمرده نشده‌اند.

## 1. خلاصهٔ وضعیت

پروژه قابل اجرا و در حال توسعه است، اما برای بهره‌برداری حساس یا مستقل هنوز تأیید نهایی نشده است. قابلیت‌های تحقیق منبع‌محور، حافظهٔ تجربه، یادگیری مسیرمحور، صف پایدار وظایف، sandbox و گردش‌کار خودبهبوددهی کنترل‌شده وجود دارند؛ با این حال ارزیابی جامع مستقل کیفیت دانش، پذیرش عملیاتی روی میزبان مقصد، و برخی دروازه‌های امنیتی و بازیابی همچنان باز هستند.

**مبنای وضعیت:** شاخهٔ `main` در commit `019e5589da3d9f13a6fe47f177ee5a8d8defb213` است. CI پس از ادغام PR #588 روی همین SHA شامل 1537 تست موفق و 5 مورد Skip، Windows storage regressions، browser E2E و dependency audit موفق است. artifact generation، بررسی لینک‌های Markdown، import-cycle، retrieval benchmark و 10 مورد claim-to-test نیز موفق شدند. CI شواهد خودکار مخزن است و جایگزین پذیرش میزبان مقصد، ارزیابی زنده یا بازبینی مستقل امنیتی نیست. برای مرز ماژول‌ها، سیاست ادغام و محدودیت این بازبینی به [گزارش یکپارچه‌سازی](INTEGRATION_REPORT.md) مراجعه کنید.

## 1.1 تغییرات جدید پس از snapshot قبلی

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
- PR [#588](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/588): DNS rebinding در درخواست‌های بازبین خارجی و بررسی منبع یادگیری مهار شد؛ IP عمومی اعتبارسنجی‌شده به اتصال TCP pin می‌شود، hostname برای TLS حفظ می‌شود، proxy و redirect غیرفعال‌اند، پورت‌ها محدود و پاسخ بازبین به 64 KiB محدود است. `httpx` از وابستگی runtime حذف و در وابستگی‌های توسعه نگه داشته شد؛ تست‌های مرزی و سند امنیتی افزوده شدند. ادغام `e08b580670999926a5350e3d00bcb48b208492eb`؛ CI head دقیق شامل tests، Windows storage، browser E2E، dependency audit و Sandbox integration همگی موفق شدند. پس از ادغام، CI روی `main` در `019e5589...` با 1537 passed و 5 skipped و سه workflow اصلی موفق شد.

## 2. قابلیت‌های پیاده‌سازی‌شده

### 2.1 یادگیری و دانش

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
