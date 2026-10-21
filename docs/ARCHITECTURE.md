# معماری فعلی سامانه

آخرین بازبینی: 2026-10-20

## اصول معماری

- اجرای local-first؛ ارتباط مدل از adapter محلی Ollama انجام می‌شود و استفاده از GPU بدون تشخیص سازگاری backend اجباری نیست.
- تنظیمات، داده‌های یادگیری/صف وظایف و کشف منابع ابری در حوزه‌های ذخیره‌سازی مجزا نگهداری می‌شوند.
- ورودی کاربر، خروجی مدل، تجربهٔ مشابه و نتیجهٔ جست‌وجوی وب به‌تنهایی دانش تأییدشده نیستند.
- هر کار پایدار باید وضعیت، منشأ، خطا و شناسهٔ تکرارپذیری مناسب داشته باشد؛ handlerهای صف فقط از allow-list اجرا می‌شوند.
- تغییرات خودبهبوددهی باید از بررسی ایستا و آزمون رفتاری ایزوله عبور کنند و برای انتشار نیازمند تأیید انسانی باشند.
- داده‌های مشتق‌شده مانند embeddingها بازسازی‌پذیرند و نباید جایگزین رکورد canonical یا شواهد منشأ شوند.

## اجزای اصلی

| حوزه | اجزا | مسئولیت |
|---|---|---|
| پیکربندی و ذخیره‌سازی | `app/config.py`, `app/db.py`, `app/schema_registry.py` | تنظیمات، SQLite، migration نسخه‌بندی‌شده، تشخیص schema ناشناخته و حفظ داده در مسیر ارتقا |
| مدل محلی | `app/ollama.py` | اتصال HTTP به Ollama، گفتگو، embedding و سیاست endpoint loopback |
| پژوهش و منابع | `app/chat_research.py`, `app/web_sources.py`, `app/claim_review.py` | کشف محدود منابع HTTPS، provenance، بررسی ادعا در برابر متن منبع و بیان عدم‌قطعیت |
| یادگیری | `app/learning.py`, `app/learning_tasks.py` | jobهای یادگیری، شواهد، محتوای نامعتبر/نیازمند بازبینی، ارزیابی و مسیرهای یادگیری |
| حافظهٔ تجربه | `app/experience.py`, `app/experience_retrieval.py`, `app/experience_feedback.py` | نسخه‌بندی تجربه، رضایت ثبت، بازیابی ترکیبی، feedback و جداسازی مورد تأییدشده از هشدارها |
| صف و اجرا | `app/task_manager.py`, `app/task_worker.py`, `app/task_runtime.py` | صف SQLite پایدار، lease، checkpoint، retry، cancellation، reconciliation و dispatch محدودشده |
| عملیات و پشتیبان | `app/storage_routes.py`, `app/db_backup.py` و ماژول‌های مرتبط | سلامت فقط‌خواندنی، پشتیبان تأییدشده، بازیابی staging و پاک‌سازی مبتنی بر preview/تأیید |
| داشبورد | `app/main.py`, `app/static/` | رابط محلی، کنترل وظایف، پژوهش، تجربه، وضعیت ذخیره‌سازی، پوسته روشن/تیره و جهت‌دهی RTL/LTR |
| خودبهبوددهی کنترل‌شده | `app/self_improvement.py` و sandbox | اعتبارسنجی patch، آزمون ایزوله، ثبت شواهد و Draft PR/rollback با تأیید صریح |

## قراردادهای داده و اعتماد

- محتوای یادگیریِ تازه یا تولیدشده، به‌صورت خودکار دانش معتبر نمی‌شود؛ شواهد، provenance و دروازه‌های بازبینی حفظ می‌شوند.
- بازیابی تجربه فقط از رکوردهای واجد شرایط استفاده می‌کند. تجربه‌های ناموفق یا نیازمند بازبینی از مسیر هشدار جداگانه برمی‌گردند و شباهت برداری مجوز تأیید نیست.
- صف وظایف فقط handlerهای نام‌گذاری‌شده و allow-list شده را dispatch می‌کند؛ payload وظیفهٔ یادگیری به شناسهٔ job محدود است و متن منبع را در صف کپی نمی‌کند.
- migrationها از registry نسخه‌بندی‌شده می‌آیند؛ schema ناشناخته یا drift بیرونی نباید با تغییر خودکار یا حذف داده دور زده شود.
- پشتیبان restore ابتدا در staging بررسی می‌شود. جایگزینی دیتابیس فعال، اجرای remote و provisioning ابری مسیرهای خودکار مجاز نیستند.
- اجرای patch تولیدشده روی checkout اصلی، merge و deploy خودکار غیرفعال‌اند. PRها برای بازبینی انسانی ساخته می‌شوند.

## وضعیت آزمون‌ها

- CI شامل آزمون‌های واحد، رگرسیون‌های SQLite در Windows، مرورگر Chromium، ممیزی وابستگی‌ها، کنترل چرخه‌های import و baseline قطعی بازیابی lexical است.
- آزمون‌های چندفرآیندی، توقف ناگهانی worker و benchmark مستقل صف در CI وجود دارند؛ این شواهد جایگزین crash/recovery/load روی میزبان هدف نیستند.
- fixture فعلی بازیابی دو زبانه و مصنوعی است. relevance انسانی و ارزیابی زندهٔ Ollama همچنان برای ادعای کیفیت واقعی لازم‌اند.
- ابزار بررسی لینک‌های Markdown فقط مقصدهای محلی را اعتبارسنجی می‌کند؛ URLهای خارجی را واکشی نمی‌کند.

## مرزها و دروازه‌های باز

- API احراز هویت راه‌دور ندارد و فقط برای دسترسی محلی طراحی شده است؛ نباید روی شبکهٔ عمومی منتشر شود.
- provisioning منابع ابری و اجرای remote عمداً غیرفعال‌اند تا سیاست مجوز، هزینه و امنیت جداگانه تأیید شود.
- پذیرش واقعی مهاجرت/راه‌اندازی Windows، ارزیابی زندهٔ مدل و داوری انسانی، آزمون‌های عملیاتی میزبان هدف، تمرین واقعی restore/rollback، بازبینی مستقل امنیتی و ارزیابی دستی دسترس‌پذیری جدا از CI باقی می‌مانند.
- موفقیت آزمون یا بازبینی مدل به‌تنهایی تأیید مستقل علمی یا گواهی امنیتی نیست.


### Citation URL validation

Research source payloads accept only HTTPS URLs with a parseable host and no embedded user-info credentials. This prevents a citation such as `https://trusted.example@attacker.example/path` from visually disguising its actual authority. Malformed ports and missing hosts are rejected before source excerpts are passed to the model. This is URL-shape validation, not a claim that the destination is trustworthy or safe to fetch; network policy and independent security review remain separate.
