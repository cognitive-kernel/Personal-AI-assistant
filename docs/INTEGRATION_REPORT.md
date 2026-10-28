# گزارش یکپارچه‌سازی و ممیزی معماری مخزن

> تاریخ بررسی: 2026-10-28  
> شاخهٔ مبنا: `main`  
> commit مبنا: `026d914426af0ccbbeb002b20bc21f7be35d5d75`  
> هدف این سند: تعیین مرزهای ادغام، منابع مرجع مستندات و مواردی که پیش از ادعای آمادگی نهایی باید آزموده شوند.

## 1. نتیجهٔ اجرایی

مخزن در commit مبنا از قبل به‌صورت ماژولار سازمان‌دهی شده است؛ `app/main.py` نقطهٔ ترکیب FastAPI، middlewareها، lifecycle و بخشی از endpointهای اصلی است و routerها و زیرسامانه‌های مستقل در فایل‌های جدا قرار دارند. انتقال تمام کدها به یک فایل `main.py` ادغام استاندارد محسوب نمی‌شود: این کار coupling را بالا می‌برد، تست واحد را دشوارتر می‌کند و خطر regression را افزایش می‌دهد. بنابراین ساختار ماژولار حفظ می‌شود و ادغام در `main` باید از طریق تغییرات کوچک، قابل بازبینی و CI-پذیرفته انجام شود.

این گزارش به‌خودی‌خود ادعای اجرای محلی کل مجموعهٔ تست یا ممیزی خط‌به‌خط تمام 276 فایل نیست. برای چنین ادعایی باید checkout کامل، اجرای تست‌ها و ارزیابی عملی روی میزبان هدف در دسترس باشد.

## 2. نمای مخزن در commit مبنا

- 297 ورودی در درخت Git، شامل 276 فایل.
- `app/`: 91 فایل؛ کد برنامه، routerها، منطق کسب‌وکار، UI و زیرسامانهٔ یادگیری.
- `tests/`: 110 فایل؛ آزمون‌های واحد، رگرسیون و یکپارچه‌سازی.
- `docs/`: 48 فایل؛ معماری، عملیات، امنیت، یادگیری و نیازمندی‌ها.
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
