# موجودی حوزه‌های داده و مسیرهای دسترسی

- تاریخ بازبینی: 2026-10-31
- مبنای کد: آخرین commit مخزن و برنامه در `main` برابر `fe1d533eb4a8cff86de978be4487898666afdddd` است (PR #645). حوزه‌های schema registry، مالکیت سه پایگاه SQLite، صف پایدار، lifecycle worker و مسیر backup/restore از همین کد بررسی شده‌اند.
- دامنه: بازبینی استاتیک کد و شواهد CI؛ داده‌های محلی/کاربر خوانده یا تغییر داده نشده‌اند و تست محلی/انسانی اجرا نشده است. post-merge CI روی `fe1d533` با 1754 passed/5 skipped، Windows storage، Chromium browser E2E، dependency audit، بررسی 84 فایل Markdown و benchmark 10/10 موفق شد.
- ممیزی دقیق schema و خطرهای مهاجرت: [SCHEMA_MIGRATION_AUDIT.md](SCHEMA_MIGRATION_AUDIT.md)

## حوزه‌ها و مالکیت فعلی

| حوزه | محل/مسیر فعلی | وضعیت مشاهده‌شده | نتیجه برای طراحی |
|---|---|---|---|
| تنظیمات | `app/db.py`; `settings_connection()`; `app/schema_registry.py` | فایل SQLite مستقل؛ جدول `settings` با مقدار JSON و seedهای idempotent؛ schema migration از registry نسخه‌بندی‌شده اجرا می‌شود | مرز فعلی مستقل بماند؛ schema ناشناخته fail-closed است؛ پذیرش فایل واقعی کاربر جداگانه باقی است. |
| یادگیری و دانش | `app/learning.py`, `app/learning_tasks.py` | `learning_jobs`, متن منبع، شواهد، درس‌ها، ارزیابی‌ها، بازخورد و مسیرهای یادگیری در فایل learning | فعلاً مالکیت مرتبط است؛ تفکیک نیازمند ماتریس دسترسی/حریم خصوصی و migration آزموده‌شده است. |
| صف وظایف | `app/task_manager.py` | `task_queue` در همان فایل learning؛ lease، checkpoint، retry و dedupe پایدار | از نظر کد ماژول جداست ولی از نظر فایل مستقل نیست؛ جداکردن فیزیکی فقط پس از تحلیل تراکنش‌ها و بازیابی. |
| کشف منابع ابری | `app/cloud_resources.py`, `app/cloud_resource_routes.py`, `cloud_resources.db` | نامزدهای مبتنی بر منابع عمومی و تاریخچهٔ discovery در فایل مستقل با migration نسخه‌بندی‌شده؛ وضعیت پیش‌فرض unreviewed/unknown | مرز مستقل به‌دلیل چرخهٔ عمر عملیاتی، تازگی داده، بررسی شرایط استفاده و سیاست مجوزدهی توجیه دارد؛ این DB ظرفیت واقعی یا مجوز اجرای کار را اثبات نمی‌کند. |
| پشتیبان‌گیری و بازیابی | `app/db_backup.py`; `tests/test_db_backup.py` | snapshotهای settings/learning و cloud-resource با manifest، SHA-256، SQLite integrity و metadata schema؛ PR #637 snapshot staged را بعد از copy دوباره با manifest تطبیق می‌دهد | manifest consistency در CI پوشش دارد، اما signature/authenticity نیست؛ restore/rollback در staging و روی میزبان هدف همچنان باید جداگانه تمرین شود. |
| اجرای وظایف | `app/task_worker.py`, `app/task_runtime.py`, `app/main.py` | فقط handlerهای allow-list شده اجرا می‌شوند؛ PR #636 ثبت runtime پیش از start را تضمین می‌کند، PR #639 مانع restart زودهنگام هنگام shutdown می‌شود، PR #641 reconciliation دوره‌ای taskهای ناقص learning را اضافه می‌کند و PR #645 failure isolation هر job و startup retry را پوشش می‌دهد | lifecycle startup/shutdown، بازیابی threadها و repair دوره‌ای queue با تست خودکار پوشش دارند؛ PR #645 failure isolation هر job را با 1754 passed/5 skipped و post-merge CI موفق تأیید کرده است؛ crash/soak روی میزبان هدف و هماهنگی چندفرایندی هنوز اثبات نشده است. |
| لغو یادگیری | `app/learning_tasks.py`, `app/task_routes.py`, `app/learning.py` | توقف مسیر/درس و API عمومی task، وضعیت task و learning job را هماهنگ می‌کنند | درخواست لغو حین درخواست HTTP مدل تعاونی است؛ قطع فوری محاسبهٔ مدل تضمین نشده است. |
| گزارش عملیاتی و ممیزی | logging و ستون‌های خطا/زمان | مخزن مستقل audit با سیاست retention و قرارداد مالکیت تأیید نشده | پیش از ایجاد DB مستقل، نیازهای نگهداری، حریم خصوصی و جست‌وجو مشخص شوند. |
| آرشیو مکالمه | مسیرهای API گفتگو و history ورودی | ذخیره‌سازی canonical مکالمه از کد ممیزی‌شده اثبات نشده است | پیش از ساخت مخزن، رفتار فعلی و الزامات حریم خصوصی/حذف بررسی شود. |
| ایندکس بازیابی | جست‌وجوی lexical/vector | مخزن canonical یا ایندکس مستقل اثبات نشده است | ایندکس باید دادهٔ مشتق‌شده و بازسازی‌پذیر باشد؛ ایجاد DB تا اثبات نیاز به تعویق افتد. |
| گراف دانش | مسیرها و پیش‌نیازهای یادگیری | رابطه‌های ساختاری محدود وجود دارد؛ گراف عمومی مستقل اثبات نشده است | ابتدا نیازهای traversal و query اندازه‌گیری شود؛ تعداد DB از پیش ثابت نیست. |

## یافته‌های schema و مسیرهای داده

- `settings.db` فقط تنظیمات را نگه می‌دارد؛ `learning.db` داده‌های یادگیری و صف را نگه می‌دارد.
- schemaها در `app/schema_registry.py` به‌صورت migrationهای نسخه‌بندی‌شده تعریف شده‌اند و `db.init_databases()` و `task_manager.initialize_task_store()` از همان registry استفاده می‌کنند. قبل از baseline/ارتقا backup اعتبارسنجی‌شده تهیه می‌شود؛ schema ناشناخته و drift بیرونی خودکار پذیرفته یا تعمیر نمی‌شود.
- `learning.submit_job()` و شروع/تولید مجدد درس‌ها به task پایدار `learning.run_job` ارسال می‌شوند؛ payload صف فقط `job_id` دارد و متن منبع را کپی نمی‌کند.
- `learning_tasks.reconcile_queued_learning_tasks()` در startup و maintenance دوره‌ای برای رکوردهای واجد شرایط queued/running، task متناظر را به‌صورت idempotent تضمین می‌کند؛ maintenance failure را جداگانه log می‌کند تا مسیرهای مستقل ادامه یابند (PR #641، ADR-017).
- `task_manager.get_task_by_dedupe_key()` و آداپتر یادگیری، توقف از API عمومی صف را با وضعیت رکورد دامنه هماهنگ می‌کنند.
- هیچ داده‌ای از settings/learning به DB کشف منابع ابری منتقل نشده است؛ این DB فقط نامزدها و تاریخچهٔ discovery خودش را نگه می‌دارد. برای تجربه، مکالمه، retrieval یا graph هنوز تصمیمی برای دیتابیس مستقل گرفته نشده است.

## ریسک‌های باقی‌مانده

1. ایجاد learning job و task دو عملیات جداست؛ startup reconciliation پنجرهٔ قطع فرایند را پوشش می‌دهد، اما fault-injection در همهٔ مرزهای commit هنوز لازم است.
2. migration runner، schema version metadata و ابزار backup/restore با آزمون بازیابی پیاده‌سازی و در CI پوشش داده شده‌اند؛ PR #637 SHA-256، `user_version` و table list snapshot staged را قبل از انتشار با manifest تطبیق می‌دهد. manifest امضای اصالت نیست و ابزارها هنوز روی فایل‌های واقعی دیتابیس کاربر اجرا/اعتبارسنجی نشده‌اند.
3. وضعیت واقعی schema در فایل‌های DB کاربر بررسی نشده است؛ پیش از مهاجرت باید fingerprint و integrity check انجام شود.
4. توقف یک task در حال اجرای مدل الزاماً پردازش مدل را فوراً قطع نمی‌کند؛ نتیجهٔ لغوشده نباید در دانش canonical ذخیره شود.

## وضعیت پس از migration runner و startup

- `settings.db`، `learning.db` و `cloud_resources.db` فایل‌های مستقل‌اند. هرکدام registry مهاجرت مستقل دارند؛ برای settings/learning ابزار backup پیش از adoption/upgrade و restore staged موجود است. PR #637 snapshot نهایی هر فایل را پس از copy با SHA-256 و metadata manifest تطبیق می‌دهد؛ این کنترل integrity جایگزین امضای رمزنگاری‌شده یا تمرین restore/rollback در staging نیست. DB کشف منابع فقط دادهٔ نامزدهای خود را نگه می‌دارد.
- `task_queue` فعلاً در `learning.db` باقی می‌ماند؛ صف و learning job به reconciliation/idempotency مشترک وابسته‌اند و جداکردن آن بدون قرارداد outbox یا همگام‌سازی و آزمون fault-injection ریسک تازه ایجاد می‌کند.
- migration fixtureها شناسه‌ها، source_ref، content_hash، متن منبع، knowledge item، assessment، review، مسیر یادگیری و task را بررسی می‌کنند. این آزمون‌ها مهاجرت schema را پوشش می‌دهند، نه انتقال فیزیکی میان دو فایل DB.
- برای archive مکالمه، audit مستقل، vector/lexical index و graph هنوز مخزن canonical اثبات نشده؛ تا زمانی که نیاز و مسیر داده مشخص نشود، دیتابیس جدید ساخته نمی‌شود. اجرای discovery ابری هم‌اکنون تنها metadata و excerpt محدود از صفحات عمومی را ذخیره می‌کند.
- فایل‌های دیتابیس واقعی کاربر در این کار دست‌کاری نشده‌اند؛ پیش از هر انتقال واقعی باید backup، dry-run، شمارش/hash و restore روی کپی انجام شود.

## گام بعدی

تکمیل ماتریس مرز داده با تصمیم مستند برای هر حوزه و افزودن fault-injection در پنجره‌های crash میان ثبت learning job، enqueue، ذخیرهٔ نتیجه و تکمیل task. تفکیک فیزیکی جدید تنها زمانی انجام شود که مزیت تراکنشی/امنیتی/عملیاتی آن اثبات شود.
