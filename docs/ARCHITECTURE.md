# نمای معماری پروژه

> این سند مرزهای اصلی معماری و مسئولیت ماژول‌ها را خلاصه می‌کند. برای سیاست ادغام و فهرست دروازه‌های پذیرش به [گزارش یکپارچه‌سازی](INTEGRATION_REPORT.md)، برای وضعیت اجرایی به [وضعیت پروژه](PROJECT_STATUS.md) و برای جزئیات یادگیری به [راهنمای جامع یادگیری](LEARNING.md) مراجعه کنید.

## اصول معماری

- اجرای محلی (local-first) و بودجه‌بندی محافظه‌کارانهٔ منابع؛ backend باید قابلیت واقعی GPU را بررسی کند و استفاده از GPU نباید بدون پشتیبانی فعال شود.
- داده‌های پایدار در SQLite و با schemaهای نسخه‌بندی‌شده نگهداری می‌شوند؛ ساختار ناشناخته باید fail-closed بماند و بدون مسیر مهاجرت بازبینی‌شده تغییر نکند.
- اتصال مدل از طریق adapter مستقل انجام می‌شود؛ پیاده‌سازی فعلی از Ollama استفاده می‌کند.
- ورودی کاربر، محتوای وب و خروجی مدل دادهٔ بالقوه نامطمئن‌اند؛ متن منبع یا رأی مدل به‌تنهایی اثبات حقیقت نیست.
- وظایف پس‌زمینه باید وضعیت پایدار، lease/checkpoint، لغو و بازیابی مشخص داشته باشند؛ dispatch فقط به handlerهای ثبت‌شده اجازهٔ اجرا می‌دهد.
- تغییرات خودکار باید محدود، قابل ممیزی و قابل rollback باشند؛ ادغام و استقرار خودکار به production فعال نیست.

## نقشهٔ ماژول‌ها

- `app/config.py`: تنظیمات و پیش‌فرض‌های محیط.
- `app/hardware.py`: تشخیص CPU/RAM و محاسبهٔ محدودیت‌های اجرا.
- `app/db.py` و `app/schema_registry.py`: دسترسی و کنترل schemaهای SQLite.
- `app/ollama.py`: ارتباط با Ollama محلی.
- `app/main.py`: composition root برنامهٔ FastAPI؛ ثبت routerها، middleware، lifecycle و endpointهای عمومی.
- `app/*_routes.py`: مرزهای HTTP برای وظایف، پژوهش، منابع ابری، sandbox، حافظهٔ تجربه، خودبهبوددهی و تأیید factual.
- `app/task_manager.py`, `app/task_runtime.py`, `app/task_worker.py`: صف پایدار، lease/checkpoint و چرخهٔ اجرای وظایف؛ `TaskContext.complete_with_commit` برای هم‌ترازی اتمیک تغییر دامنه با موفقیت task.
- `app/chat_research.py`, `app/research_planner.py`, `app/research_tasks.py`, `app/research_routes.py`, `app/web_sources.py`: برنامه‌ریزی و اجرای پژوهش، کشف/واکشی منابع عمومی و مسیرهای پژوهش.
- `app/learning.py`, `app/learning_tasks.py`, `app/learning_review_adapters.py` و ماژول‌های مرتبط: یادگیری، ارزیابی، مرور فاصله‌دار، برنامهٔ درسی و مدیریت شواهد.
- `app/learning_knowledge_graph.py`, `app/learning_knowledge_graph_tasks.py`, `app/learning_knowledge_graph_routes.py`: پیشنهاد و مرور روابط دانش؛ فقط یال‌های پذیرفته‌شده وارد context می‌شوند و commit نتیجهٔ task با تغییر یال در یک تراکنش انجام می‌شود.
- `app/factual_knowledge.py`, `app/factual_source_checks.py`, `app/factual_semantic_review.py`, `app/factual_verification_tasks.py`: نگهداری دانش factual، بررسی منبع و مرور معنایی.
- `app/cloud_resources.py`, `app/cloud_provider_adapters.py`, `app/cloud_resource_policy.py`, `app/cloud_resource_planner.py`: snapshot سهمیه، adapter ارائه‌دهنده و سیاست برنامه‌ریزی منابع ابری.
- `app/experience_capture_consent.py`, `app/experience_tasks.py`, `app/experience_retrieval.py`, `app/experience_source_checks.py`: رضایت، ثبت، بازیابی و کنترل منبع تجربه.
- `app/ai_learning/`: زیرسامانهٔ اختیاری مستقل با feature flag، ذخیره‌سازی، browser automation و رابط خودش.
- `app/static/`: رابط داشبورد اصلی.

## Snapshot موجودی ماژول‌ها

بازبینی ساختار در شاخهٔ `main` روی commit `a629b0dc1f435c7fb845a84519cd9f0dc34408bd`، تعداد 112 فایل Python زیر `app/` و یک زیرپکیج `app/ai_learning/` را نشان می‌دهد. `app/main.py` نقطهٔ ترکیب و ثبت routerهاست؛ منطق دامنه در ماژول‌های بالا و زیرپکیج‌های مربوطه باقی می‌ماند. این snapshot فقط موجودی و مرزبندی اولیه است؛ ممیزی کامل file/line، وابستگی‌ها، مالکیت داده و رتبه‌بندی ریسک در workstream E-01 همچنان در حال انجام است.

## مسئولیت main.py

`app/main.py` باید نقطهٔ ترکیب برنامه باقی بماند، نه محل قرارگیری تمام منطق کسب‌وکار. منطق دامنه و endpointهای مستقل باید در ماژول‌های مربوطه بمانند؛ تغییرات معماری باید به‌صورت استخراج مرحله‌ای همراه با آزمون‌های regression انجام شوند. انتقال همهٔ فایل‌ها به یک فایل، ساده‌سازی محسوب نمی‌شود و coupling را افزایش می‌دهد.

## مرزهای امنیتی

- API احراز هویت عمومی/راه‌دور ندارد و نباید روی شبکهٔ عمومی منتشر شود.
- middleware باید درخواست‌های peerهای غیر-loopback را رد کند؛ این کنترل باید در آزمون‌ها حفظ شود.
- خروجی مدل، URL و محتوای جمع‌آوری‌شده نباید به‌عنوان دستور قابل اعتماد یا منبع حقیقت تلقی شود.
- provisioning ابری، اجرای راه‌دور و ادغام/استقرار خودکار production تا زمان تعریف و پذیرش دروازه‌های مستقل غیرفعال می‌مانند.

## مستندات مرجع

- وضعیت و دروازه‌های پذیرش: [PROJECT_STATUS.md](PROJECT_STATUS.md)
- راهنمای جامع یادگیری: [LEARNING.md](LEARNING.md)
- توسعه و اجرای آزمون‌ها: [DEVELOPMENT.md](DEVELOPMENT.md)
- گزارش یکپارچه‌سازی و حذف مستند تکراری: [INTEGRATION_REPORT.md](INTEGRATION_REPORT.md)
- تصمیم بازیابی خرابی جزئی threadهای صف وظایف: [ADR-012](architecture/ADR-012-task-runtime-health-recovery.md)
- تقدم درخواست توقف/لغو بر retry خودکار صف: [ADR-013](architecture/ADR-013-task-control-priority.md)
- commit اتمیک تغییر دامنه و موفقیت task پایدار: [ADR-019](architecture/ADR-019-atomic-domain-commit-with-task-completion.md)
- ثبت runtime پیش از شروع threadها برای پاک‌سازی شکست startup: [ADR-014](architecture/ADR-014-track-runtime-before-start.md)
- تطبیق snapshot نهایی بازیابی با manifest و رد تغییر هم‌زمان فایل پشتیبان: [ADR-015](architecture/ADR-015-verify-staged-restore.md)
- جلوگیری از idempotent no-op شدن start هنگام shutdown در حال اجرا: [ADR-016](architecture/ADR-016-runtime-restart-after-stop.md)
- ترمیم دوره‌ای فاصلهٔ بین ایجاد learning job و enqueue صف پایدار: [ADR-017](architecture/ADR-017-periodic-learning-task-reconciliation.md)
