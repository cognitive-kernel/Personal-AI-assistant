# وضعیت یکپارچهٔ پروژه

> تاریخ بازبینی: 2026-10-29  
> شاخهٔ مبنای مستندات: `main`، commit `7005626755f9616e9bbca95c4a4521a6af64e20c` (2026-10-29).  
> این سند مرجع وضعیت کلی پروژه و دروازه‌های عملیاتی است؛ مرجع واحد جزئیات یادگیری، محدودیت‌ها و نقشهٔ راه آن `docs/LEARNING.md` است. تیک فقط برای قابلیتی است که وجود آن در کد یا قرارداد فعلی مخزن شواهد دارد؛ تیک به‌تنهایی به معنی پذیرش عملیاتی روی دستگاه مقصد یا اثبات صحت علمی نیست. تست‌های محلی و تست‌های انسانی در فهرست کارهای باقی‌ماندهٔ این سند شمرده نشده‌اند.

## 1. خلاصهٔ وضعیت

پروژه قابل اجرا و در حال توسعه است، اما برای بهره‌برداری حساس یا مستقل هنوز تأیید نهایی نشده است. قابلیت‌های تحقیق منبع‌محور، حافظهٔ تجربه، یادگیری مسیرمحور، صف پایدار وظایف، sandbox و گردش‌کار خودبهبوددهی کنترل‌شده وجود دارند؛ با این حال ارزیابی جامع مستقل کیفیت دانش، پذیرش عملیاتی روی میزبان مقصد، و برخی دروازه‌های امنیتی و بازیابی همچنان باز هستند.

**مبنای وضعیت:** این بازبینی به شاخهٔ `main` در commit `7005626755f9616e9bbca95c4a4521a6af64e20c` اشاره دارد. پس از ادغام PR #543، آزمون کامل [#40907604873](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40907604873)، Windows storage regressions، browser E2E [#40907604932](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40907604932) و dependency audit [#40907604799](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/40907604799) روی همین commit موفق شدند. این CI شواهد خودکار مخزن است و جایگزین پذیرش میزبان مقصد، ارزیابی زنده یا بازبینی مستقل امنیتی نیست. برای مرز ماژول‌ها، سیاست ادغام و محدودیت این بازبینی به [گزارش یکپارچه‌سازی](INTEGRATION_REPORT.md) مراجعه کنید.

## 1.1 تغییرات جدید پس از snapshot قبلی

- PR [#515](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/515): آزمون fault-injection برای شکست موقت ذخیرهٔ نتیجهٔ وظیفه و جلوگیری از رقابت در monkeypatch سراسری؛ CI روی head دقیق موفق شد.
- PR [#516](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/516): اعمال پیش‌نیازها پیش از تغییر وضعیت سرفصل و جلوگیری از حذف درس موجود هنگام تولید مجددِ مسدودشده؛ CI روی head دقیق موفق شد.
- PR [#517](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/517): بازیابی retryable هنگام شکست ساخت/شروع thread تمدید lease؛ handler بدون heartbeat اجرا نمی‌شود؛ CI روی head دقیق موفق شد.
- PR [#518](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/518): جلوگیری از تکمیل دستی بدون assessment موفق، قفل‌کردن نمره‌های دارای منشأ ارزیابی و اعتبارسنجی نمره؛ CI روی head دقیق موفق شد.
- PR [#519](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/519): migration نسخهٔ 39 برای ثبت و backfill منشأ نمره، و یکپارچه‌سازی assessment عملی sandbox با گیت عمومی تکمیل؛ CI روی head دقیق موفق شد.
- PR [#521](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/521): ثبت assessment عملی با idempotency key مبتنی بر شناسهٔ وظیفه؛ retry همان task تلاش تکراری نمی‌سازد و payload متفاوت با همان کلید رد می‌شود؛ test، audit وابستگی، browser E2E و sandbox integration CI موفق شدند.
- PR [#522](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/522): پس از تشخیص قطع‌شدن context مرورگر، یک relaunch کنترل‌شده مجاز است؛ پس از قطع‌شدن دوباره، سیستم برای جلوگیری از چرخهٔ بازشدن Chrome به بازیابی دستی نیاز دارد؛ test، audit وابستگی و browser E2E موفق شدند.
- PR [#524](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/524): بازیابی startup و انتخاب کار بعدی صف یادگیری مستقیماً از SQLite و بدون سقف صفحه‌بندی داشبورد انجام می‌شود؛ آزمون‌های رگرسیون برای بیش از ۵۰ run در یک دوره و بیش از ۱۰۰ دوره اضافه شدند؛ test، dependency audit، browser E2E و Windows storage regressions موفق شدند.
- PR [#528](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/528): lease پایدار SQLite با token مختص فرایند و heartbeat برای جلوگیری از claim هم‌زمان دورهٔ یادگیری مرورگر اضافه شد؛ تست، dependency audit، browser E2E و Windows storage regressions روی head دقیق موفق شدند.
- PR [#531](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/531): lease پس از اتمام recovery هنگام startup آزاد می‌شود تا فرایند بیکار تا انقضای چهار دقیقه‌ای مالکیت را نگه ندارد؛ تست رگرسیون و ADR-004 به‌روزرسانی شدند؛ تمام workflowهای لازم روی head دقیق موفق شدند.
- PR [#526](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/526): مسیر قدیمی‌تر `/api/learning/browser/courses` اکنون هنگام startup دوره‌های قطع‌شده را به‌طور خودکار بازیابی می‌کند؛ درس‌های کامل حفظ می‌شوند، دوره‌ها با dispatcher تک‌نمونه‌ای به ترتیب اجرا می‌شوند و پس از شکست یک دوره، صف برای جلوگیری از شکست زنجیره‌ای متوقف می‌شود. test، dependency audit، browser E2E و Windows storage regressions موفق شدند.
- PR [#534](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/534): recovery فقط دوره‌های واقعاً `running` را به `interrupted` تبدیل می‌کند و دوره‌های از قبل `queued` را دست‌نخورده نگه می‌دارد؛ درس‌های تکمیل‌شده حفظ می‌شوند. تست کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `d07e3d692650479a010ba2e182e5fee276318fc7` موفق شدند. ادغام در `main` با commit `54b56831fe7dcaa719b031d659a3f7e9bf19703d` انجام شد؛ CI پس از ادغام باید جداگانه بررسی شود.
- PR [#536](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/536): تلاش مجدد محدود و زمان‌بندی‌شده برای دوره‌های یادگیری مرورگر، بازیابی اجرای مانده با رعایت lease و جلوگیری از تغییر وضعیت دورهٔ تکمیل‌شده توسط worker قدیمی اضافه شد. آزمون‌ها، Windows storage regressions، browser E2E و dependency audit روی head دقیق موفق شدند؛ ادغام `e0c86fb6fae4a83e78f07d56eb3f95af0f1861a3` انجام شد و سه workflow پس از ادغام `main` نیز موفق شدند.
- PR [#537](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/537): مسیر گفت‌وگوی عادی نیز زبان پاسخ را بررسی می‌کند، یک اصلاح محدود انجام می‌دهد و در صورت شکست اصلاح یا پاسخ خالی به fallback ثابت برمی‌گردد. آزمون‌ها، Windows storage regressions، browser E2E و dependency audit روی head دقیق موفق شدند؛ ادغام `4499e0d59254c1186eb82a0612909dc96f08179b` انجام شد؛ آزمون‌ها، Windows storage regressions، browser E2E و dependency audit پس از ادغام روی `main` نیز موفق شدند.
- PR [#539](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/539): کنترل‌های مفید/غیرمفید برای پاسخ‌های دستیار به پیام و گفت‌وگوی مشخص متصل شدند؛ migration نسخهٔ 5 بازخورد را جدا از دانش ذخیره می‌کند و حذف پیام/گفتگو را با cascade رعایت می‌کند. API فقط پیام دستیار را می‌پذیرد، امتیاز قابل تغییر است و هیچ ارتقای خودکار دانش رخ نمی‌دهد. آزمون‌های دقیق PR: 1415 موفق، 5 مورد skip؛ Windows storage regressions، browser E2E و dependency audit نیز موفق شدند. ادغام `daeb3dcf57200ac6cd6ca9b0c4e8dedf76123d8f` انجام شد و سه workflow پس از ادغام `main` نیز موفق شدند.
- PR [#541](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/541): اعتبارسنجی امتیاز بازخورد در API به `StrictInt` تغییر کرد تا مقدارهای boolean و رشتهٔ عددی به‌اشتباه پذیرفته نشوند؛ آزمون کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `0d4aa31da97fe1113c533a356e44d469fc6ec575` موفق شدند. ادغام در `main` با commit `7005626755f9616e9bbca95c4a4521a6af64e20c` انجام شد؛ آزمون کامل، browser E2E و dependency audit پس از ادغام روی همین commit نیز موفق شدند.
- PR [#543](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/543): پاسخ خالی یا غیرمتنی مدل در چت عادی و پژوهشی دیگر به‌عنوان پاسخ معتبر عبور نمی‌کند؛ fallback بومی‌سازی‌شده برگردانده می‌شود و مسیر پژوهش وضعیت `generation_failed` را بدون متن خام ثبت می‌کند و بررسی ادعا را اجرا نمی‌کند. آزمون کامل، Windows storage regressions، browser E2E و dependency audit روی head دقیق `419a3f35838a32d2da97c1aec9a6ce75006bb90c` موفق شدند؛ آزمون کامل 1423 موفق و 5 مورد skip گزارش کرد. ادغام با commit `7005626755f9616e9bbca95c4a4521a6af64e20c` انجام شد؛ آزمون کامل، Windows storage regressions، browser E2E و dependency audit پس از ادغام نیز موفق شدند.
- این شواهد، آزمون خودکار مخزن هستند؛ جایگزین ارزیابی زنده، پذیرش میزبان هدف، تمرین restore/rollback یا بازبینی امنیتی مستقل نیستند.

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
