# ممیزی schema فعلی و طرح مهاجرت نسخه‌بندی‌شده

- تاریخ ممیزی: 2026-10-15
- شاخه مبنا: `main` در commit `2050ebea67b766fd0c2d7b21b3fbc496c76c0f7a`
- دامنه: خواندن استاتیک `app/db.py`، `app/task_manager.py`، آداپتر یادگیری و تست‌های schema؛ اجرای محلی یا بررسی فایل‌های واقعی دادهٔ کاربر انجام نشده است.
- هدف: ثبت schema واقعی پیش از افزودن migration runner؛ این سند ادعا نمی‌کند که مهاجرتی اجرا شده است.

## 1. پایگاه‌ها و مالکیت فعلی

| فایل/پایگاه | جدول | مالک منطقی فعلی | وابستگی‌ها و نکتهٔ اصلی |
|---|---|---|---|
| تنظیمات SQLite | `settings`, `schema_migrations` | `app/db.py`, `app/migrations.py` | تنظیمات با کلید اصلی `key`؛ تاریخچهٔ migration مستقل؛ پیش‌فرض‌ها با `INSERT OR IGNORE` seed می‌شوند. |
| یادگیری SQLite | `learning_jobs` | `app/learning.py` | رکورد اصلی درس/کار؛ `path_topic_id` به `learning_path_topics.id` ارجاع می‌دهد؛ ستون‌های `quality_json` و `path_topic_id` در نسخه‌های قدیمی با ALTER افزوده می‌شوند. |
| یادگیری SQLite | `learning_evidence` | `app/learning.py` | `job_id` به `learning_jobs.id` با `ON DELETE CASCADE`؛ منشأ و hash شواهد. |
| یادگیری SQLite | `learning_source_text` | `app/learning.py` | `job_id` کلید اصلی و FK با cascade؛ متن منبع کاربر دادهٔ حساس/حجیم است و نباید در payload وظیفه کپی شود. |
| یادگیری SQLite | `knowledge_items` | `app/learning.py` | FK یکتای `job_id`؛ یکتایی `(topic, version)`؛ وضعیت پیش‌فرض `unverified`. |
| یادگیری SQLite | `assessment_attempts` | `app/learning.py` | FK به job با cascade؛ پاسخ‌های یادگیرنده و امتیازهای تاریخی. |
| یادگیری SQLite | `lesson_reviews` | `app/learning.py` | FK به job با cascade؛ قید `value_rating BETWEEN 1 AND 5`. |
| یادگیری SQLite | `learning_paths` | `app/learning.py` | `curriculum_slug` یکتا. |
| یادگیری SQLite | `learning_path_topics` | `app/learning.py` | FK به مسیر با cascade؛ یکتایی `(path_id, topic)` و `(path_id, position)`. |
| یادگیری SQLite | `task_queue` | `app/task_manager.py` | صف پایدار با lease، checkpoint، retry و dedupe؛ ایجاد آن در `initialize_task_store()` انجام می‌شود، نه در `db.init_databases()`. |

## 2. ایندکس‌ها و قیود مهم

- `learning_jobs`: `idx_learning_jobs_status` و `idx_learning_jobs_path_topic(path_topic_id, status)`.
- `knowledge_items`: `idx_knowledge_topic(topic, version DESC)`، یکتایی `job_id` و `(topic, version)`.
- `assessment_attempts`: `idx_assessment_attempts_job(job_id, id DESC)`.
- `lesson_reviews`: `idx_lesson_reviews_job(job_id, id DESC)`.
- `learning_path_topics`: `idx_path_topics_status(path_id, status, position)`.
- `task_queue`: `idx_task_queue_ready`، `idx_task_queue_lease` و ایندکس یکتای جزئی `idx_task_queue_dedupe_key` برای مقادیر غیر NULL.
- اتصال‌های SQLite، `PRAGMA foreign_keys=ON` و `busy_timeout=10000` را فعال می‌کنند. فایل‌های DB با مسیرهای مستقل `SETTINGS_DB_PATH` و `LEARNING_DB_PATH` تنظیم می‌شوند.

## 3. بدهی‌های schema و خطرهای مهاجرت

1. `settings.db` اکنون جدول `schema_migrations` و migration runner تراکنشی نسخهٔ 1 دارد؛ `learning.db` هنوز فاقد تاریخچهٔ migration عمومی است.
2. ساخت schema در دو محل پخش است: `db.init_databases()` و `task_manager.initialize_task_store()`. ترتیب اجرای هر دو در startup مهم است.
3. تغییرات سازگاری با schema قدیمی به شکل شرطی `ALTER TABLE` انجام می‌شوند: `learning_jobs.quality_json`، `learning_jobs.path_topic_id` و `task_queue.dedupe_key`. این روش وضعیت واقعی را ثبت نمی‌کند و تشخیص drift را دشوار می‌سازد.
4. `learning_jobs.path_topic_id` به جدولی اشاره می‌کند که در کد فعلی پایین‌تر در همان تابع ساخته می‌شود. پیش از تغییر ترتیب یا constraintها باید این وابستگی با fixtureهای SQLite واقعی آزموده شود.
5. `task_queue` علاوه بر ستون‌های قدیمی، قیدهای status و ایندکس dedupe دارد. تست موجود، مهاجرت یک schema قدیمی صف را پوشش می‌دهد؛ اما مهاجرت همهٔ نسخه‌های تاریخی پایگاه یادگیری هنوز اثبات نشده است.
6. `settings` و داده‌های یادگیری در دو فایل جدا هستند؛ صف در فایل یادگیری باقی مانده است. هیچ تصمیمی برای جداسازی فیزیکی صف یا ایجاد DBهای فرضی برای تجربه/گفتگو/retrieval/graph از این ممیزی نتیجه نمی‌شود.
7. از این بررسی، ابزار backup/restore با آزمون بازیابی و ابزار مهاجرت تراکنشی عمومی اثبات نشدند. پیش از مهاجرت دادهٔ موجود، هر دو لازم‌اند.

## 4. وضعیت و قرارداد migration runner

- این قرارداد اکنون برای `settings.db` در `app/migrations.py` پیاده‌سازی شده است: تاریخچهٔ مستقل با `version`، `migration_id`، `checksum`، زمان اعمال و حالت `migration/baseline`.
- `learning.db` هنوز از مسیر initialization فعلی استفاده می‌کند و باید در مرحله‌ای جداگانه به همان runner منتقل شود.
- migrationها مرتب، شماره‌دار، قابل‌بررسی و تا حد امکان تراکنشی باشند؛ هر migration پیش‌شرط، تغییر، پس‌شرط و راهبرد forward-recovery/rollback مستند داشته باشد.
- migration runner پیش از تغییر schema، قفل/ترتیب اجرا را کنترل کند، نسخه و checksum را اعتبارسنجی کند و در صورت شکست با خطای واضح متوقف شود؛ migration ناشناخته یا checksum متفاوت نباید بی‌صدا رد شود.
- پایگاه‌های قدیمی بدون تاریخچه نباید صرفاً به‌صورت کورکورانه «نسخهٔ جاری» علامت‌گذاری شوند. ابتدا با introspection و fixtureهای شناخته‌شده fingerprint شوند؛ schema ناشناخته به حالت fail-safe برود و نیاز به بررسی دستی را گزارش کند.
- برای پایگاه جدید، migrationها از صفر schema را بسازند. برای پایگاه موجود، baseline/adoption فقط پس از تطبیق کامل ستون‌ها، قیدها، FKها، ایندکس‌ها و آزمون `PRAGMA integrity_check` پذیرفته شود.
- ایجاد پیش‌فرض‌های تنظیمات از migration ساختاری جدا بماند؛ seed باید idempotent باشد و مقدار موجود کاربر را بازنویسی نکند.
- مهاجرت‌های مخرب در این مرحله ممنوع‌اند. مسیر expand/migrate/contract، backup قابل‌خواندن، restore روی نسخهٔ کپی، شمارش ردیف‌ها، شناسه‌ها، hashها، FKها و queryهای نماینده باید پیش از تغییر مسیر برنامه اثبات شوند.

## 5. ترتیب اجرایی پیشنهادی

1. تست‌های schema contract برای هر دو DB و fixtureهای legacy را تکمیل کن؛ قراردادهای schema و baseline تنظیمات اکنون پوشش دارند.
2. ابزار backup/restore محلی اضافه شده و تست restore دارد؛ اجرای واقعی روی دادهٔ کاربر انجام نشده است.
3. migration runner و baseline تشخیصی برای تنظیمات پیاده‌سازی شده؛ مرحلهٔ بعد انتقال امن schema یادگیری و صف به migrationهای نسخه‌دار.
4. schemaهای فعلی را به migrationهای شماره‌دار تبدیل کن و مسیر fresh-install و upgrade از fixtureهای قدیمی را در CI تست کن.
5. پس از اثبات upgrade و restore، حذف `ALTER TABLE`های ad-hoc از startup در PR مستقل انجام شود.
6. فقط پس از ممیزی کامل مسیرهای خواندن/نوشتن، دربارهٔ مرزهای دیتابیس و مهاجرت داده تصمیم بگیر؛ تعداد DBها ثابت نیست.

## 6. معیار پذیرش

- اجرای چندبارهٔ startup هیچ schema یا داده‌ای را تکراری/خراب نکند.
- upgrade از fixtureهای legacy، ردیف‌ها، شناسه‌ها، provenance، FKها و ایندکس‌های مورد انتظار را حفظ کند.
- شکست migration در نقطهٔ میانی قابل‌تشخیص باشد و با اجرای مجدد امن یا راهبرد بازیابی مستند ادامه یابد.
- backup روی کپی قابل restore باشد و `PRAGMA integrity_check` و شمارش‌های نماینده موفق باشند.
- این ممیزی فقط از کد و تست‌های موجود تهیه شده است؛ فایل‌های دادهٔ کاربر بررسی نشده‌اند و هیچ مهاجرتی در این مرحله اجرا نشده است.
