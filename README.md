# Personal AI Assistant

دستیار هوش مصنوعی شخصی با رویکرد **local-first**؛ هدف این مخزن ساخت تدریجی یک Agent قابل توسعه، قابل آزمون و سازگار با منابع سخت‌افزاری سیستم است.

## سخت‌افزار هدف
- RAM: 32 GB
- CPU: Intel Core i5-12400 (12th Gen)
- GPU: Intel Integrated Graphics، حافظه گزارش‌شده 128 MB

برنامه منابع واقعی را هنگام اجرا تشخیص می‌دهد، تعداد thread و context را محدود می‌کند و GPU را به‌صورت پیش‌فرض مجبور به استفاده نمی‌کند. اجرای کارهای سنگین باید بودجه منابع داشته باشد.

## وضعیت فعلی

این پروژه همچنان در حال توسعه است و هنوز برای بهره‌برداری حساس یا مستقل تأیید نشده است. برای وضعیت دقیق مراحل، شواهد CI و معیارهای پذیرش باقی‌مانده به [نقشهٔ راه و دروازه‌های پذیرش](docs/ROADMAP_ACCEPTANCE.md) و [گزارش پیاده‌سازی](docs/IMPLEMENTATION_LOG.md) مراجعه کنید.

قابلیت‌های فعلی شامل این موارد است:

- اتصال local-first به Ollama، تشخیص سخت‌افزار و محدودسازی منابع.
- صف پایدار وظایف با checkpoint، lease، توقف/ادامه/لغو و آزمون‌های بازیابی.
- پژوهش منبع‌محور، ثبت منشأ، بررسی تعارض منابع و ابزار تشخیصی ارزیابی پاسخ.
- حافظهٔ تجربهٔ نسخه‌دار با رضایت صریح، بازبینی منابع، تازگی شواهد و بازخورد بازیابی؛ رأی مدل به‌تنهایی تأیید علمی محسوب نمی‌شود.
- Sandbox مبتنی بر Docker با محدودیت شبکه و منابع؛ CI شامل آزمون واقعی Docker با image قفل‌شده به digest است.
- گردش‌کار خودبهبود کنترل‌شده: بررسی patch، آزمون ایزوله، تأیید صریح و Draft PR/rollback؛ ادغام و استقرار خودکار وجود ندارد.
- سلامت فقط‌خواندنی ذخیره‌سازی، پشتیبان کامل سه دیتابیس، بازیابی در staging و کنترل نگهداری با تأیید.
- آزمون Chromium واقعی برای جریان‌های اصلی داشبورد، سلامت ذخیره‌سازی، ایجاد تجربه، رضایت/لغو رضایت و بازیابی پشتیبان در staging.

**مرزهای ایمنی:** تمام درخواست‌های HTTP از peerهای غیر-loopback با 403 رد می‌شوند. provisioning ابری و اجرای راه‌دور عمداً غیرفعال‌اند. API احراز هویت راه‌دور ندارد و نباید این محدودیت دور زده شود.

**پذیرش باقی‌مانده:** اجرای baseline و مهاجرت روی میزبان هدف، ارزیابی زندهٔ Ollama همراه با داوری انسانی، آزمون‌های عملیاتی crash/recovery/load روی میزبان، تمرین واقعی restore/rollback، بازبینی امنیتی مستقل [Issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52) و ارزیابی دستی دسترس‌پذیری. CI اکنون تست‌های مرورگر، کنترل چرخه‌های import، ممیزی وابستگی‌ها و بررسی لینک‌های محلی Markdown را نیز اجرا می‌کند؛ این کنترل‌ها جایگزین پذیرش عملیاتی یا انسانی نیستند. موفقیت CI به‌تنهایی کیفیت factual مدل یا آمادگی محصول را ثابت نمی‌کند. برای آماده‌سازی گزارش ماشین هدف از ابزار [Target-host baseline](docs/TARGET_HOST_BASELINE.md) استفاده کنید؛ این ابزار دیتابیس‌ها را فقط‌خواندنی بررسی می‌کند و اجرای تست‌ها نیازمند گزینهٔ صریح `--run-tests` است. برای ثبت داوری انسانیِ ارزیابی زنده نیز [راهنمای Human Adjudication](docs/HUMAN_ADJUDICATION.md) را دنبال کنید؛ ابزار worksheet به‌تنهایی نمرهٔ کیفیت تولید نمی‌کند.

## شروع سریع
Python 3.11+ و Ollama را نصب کنید؛ سپس فقط لانچر را اجرا کنید:

```bash
python run.py
```

لانچر پیش از اجرای برنامه، محیط مجازی `.venv` را می‌سازد و وابستگی‌ها را بررسی می‌کند. حتی اگر بسته‌ای از قبل نصب باشد، نسخه سازگار آن را در `prerequisites/wheels` cache می‌کند؛ اگر cache کامل باشد، دانلود تکرار نمی‌شود و فقط بسته‌های لازم دریافت می‌شوند. نصب‌کننده رسمی Ollama نیز در `prerequisites/installers` نگهداری می‌شود. نصب در Windows و Linux به‌صورت خودکار تلاش می‌شود؛ در macOS نصب‌کننده باز می‌شود تا مراحل تعاملی تکمیل شود. مدل پیش‌فرض `qwen2.5:7b` نیز در صورت نبودن دریافت می‌شود. برای جزئیات و محدودیت‌های نصب به `docs/DEVELOPMENT.md` مراجعه کنید.

برای نصب وابستگی‌های توسعه نیز اجرا کنید:

```bash
python run.py --dev
```

برای بررسی پیش‌نیازها بدون بالا آوردن رابط وب:

```bash
python run.py --check-only
```

رابط: http://127.0.0.1:8000  
مستندات API: http://127.0.0.1:8000/docs

برای جزئیات به docs/DEVELOPMENT.md و docs/ARCHITECTURE.md مراجعه کنید. نقشه یادگیری و کاتالوگ دوره‌ها در docs/LEARNING.md قرار دارند. کاتالوگ از مسیرهای `/api/learning/curricula` و `/api/learning/curricula/{slug}` قابل دریافت است.

## پایگاه دادهٔ موجود و مهاجرت امن

برای ساختار قدیمیِ مشخص‌شده در گزارش ماشین هدف، کد فعلی یک مهاجرت محافظت‌شده دارد: فقط fingerprint دقیقاً بازبینی‌شده پذیرفته می‌شود، از فایل اصلی backup پیش از تغییر ساخته و بررسی می‌شود، داده‌های سازگار به دیتابیس موقت منتقل می‌شوند و جایگزینی فایل فقط پس از بررسی ردیف‌ها، integrity، کلیدهای خارجی و ساختار نهایی انجام می‌شود. تاریخچهٔ شناسه‌های افزایشی SQLite نیز حفظ می‌شود.

- فایل backup با نامی مانند `learning.db.pre-migration-<timestamp>.bak` کنار دیتابیس ساخته می‌شود.
- ساختار ناشناخته یا دادهٔ ناسازگار نباید به‌صورت خودکار پذیرفته شود؛ در این حالت برنامه باید fail-closed بماند.
- **دیتابیس را حذف یا از نو ایجاد نکنید و تاریخچهٔ migration را دستی ننویسید.**
- پس از دریافت نسخهٔ حاوی مهاجرت، برنامه را یک‌بار اجرا کنید؛ سپس سلامت API و backup را بررسی کنید. این کار باید ابتدا در محیط قابل‌بازیابی انجام شود.

برای بررسی فقط‌خواندنیِ ساختار ناشناخته در PowerShell:

```powershell
.\.venv\Scripts\python.exe .\scripts\inspect_sqlite_schema.py .\data\learning.db --kind learning --output C:\Temp\learning-schema-report.json
```

گزارش schema و metadata را می‌سنجد و محتوای ردیف‌های کاربر را چاپ نمی‌کند؛ به‌تنهایی migration یا تعمیر انجام نمی‌دهد. پذیرش نهایی همچنان به اجرای واقعی روی Windows هدف، بررسی backup و موفقیت `/api/health` نیاز دارد.

## امنیت اولیه
- رابط وب و همهٔ endpointها در middleware به اتصال loopback محدود شده‌اند؛ اتصال‌های غیر-loopback با 403 رد می‌شوند، حتی اگر سرور به‌اشتباه روی یک رابط شبکه bind شده باشد.
- نسخه اولیه فقط endpoint محلی Ollama را قبول می‌کند.
- API احراز هویت راه‌دور ندارد؛ برای دسترسی از LAN یا اینترنت این کنترل را دور نزنید. دسترسی راه‌دور به طراحی احراز هویت و بازبینی امنیتی مستقل جداگانه نیاز دارد.
- داده‌های محلی و فایل‌های پایگاه داده نباید به مخزن commit شوند.

## Learning-system quality and acceptance

The systemic completion plan defines the engineering phases and measurable acceptance gates for scientific source provenance, fail-closed lesson quality, calibrated learning assessment, bilingual AI regression tests, practical sandbox evaluation, and performance/reliability on the actual local Windows/Ollama host. Hardware figures in documentation are a baseline to confirm, not live measurements; no discrete GPU is assumed. Proposed thresholds must be calibrated against a versioned benchmark and are not current results.

See the [Learning system completion plan](docs/LEARNING_SYSTEM_COMPLETION_PLAN.md) and [Target-host acceptance baseline](docs/TARGET_HOST_BASELINE.md).

