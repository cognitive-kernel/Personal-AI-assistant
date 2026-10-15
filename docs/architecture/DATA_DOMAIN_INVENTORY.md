# موجودی حوزه‌های داده و مسیرهای دسترسی

- تاریخ بازبینی: 2026-10-15
- مبنای کد: `main` پس از ادغام PRهای #84، #85، #87 و #89
- دامنه: بازبینی استاتیک کد و تست‌ها؛ checkout محلی و بررسی فایل‌های دادهٔ کاربر انجام نشده است.
- ممیزی دقیق schema و خطرهای مهاجرت: [SCHEMA_MIGRATION_AUDIT.md](SCHEMA_MIGRATION_AUDIT.md)

## حوزه‌ها و مالکیت فعلی

| حوزه | محل/مسیر فعلی | وضعیت مشاهده‌شده | نتیجه برای طراحی |
|---|---|---|---|
| تنظیمات | `app/db.py`; `settings_connection()` | فایل SQLite مستقل؛ جدول `settings` با مقدار JSON و seedهای idempotent | مرز فعلی مستقل بماند؛ schema version و migration runner لازم است. |
| یادگیری و دانش | `app/learning.py`, `app/learning_tasks.py` | `learning_jobs`, متن منبع، شواهد، درس‌ها، ارزیابی‌ها، بازخورد و مسیرهای یادگیری در فایل learning | فعلاً مالکیت مرتبط است؛ تفکیک نیازمند ماتریس دسترسی/حریم خصوصی و migration آزموده‌شده است. |
| صف وظایف | `app/task_manager.py` | `task_queue` در همان فایل learning؛ lease، checkpoint، retry و dedupe پایدار | از نظر کد ماژول جداست ولی از نظر فایل مستقل نیست؛ جداکردن فیزیکی فقط پس از تحلیل تراکنش‌ها و بازیابی. |
| اجرای وظایف | `app/task_worker.py`, `app/task_runtime.py`, `app/main.py` | runtime به lifecycle FastAPI متصل؛ فقط handlerهای allow-list شده اجرا می‌شوند | اجرای پایدار و بازیابی startup موجود است؛ آزمون‌های fault-injection و مرزهای commit همچنان لازم‌اند. |
| لغو یادگیری | `app/learning_tasks.py`, `app/task_routes.py`, `app/learning.py` | توقف مسیر/درس و API عمومی task، وضعیت task و learning job را هماهنگ می‌کنند | درخواست لغو حین درخواست HTTP مدل تعاونی است؛ قطع فوری محاسبهٔ مدل تضمین نشده است. |
| گزارش عملیاتی و ممیزی | logging و ستون‌های خطا/زمان | مخزن مستقل audit با سیاست retention و قرارداد مالکیت تأیید نشده | پیش از ایجاد DB مستقل، نیازهای نگهداری، حریم خصوصی و جست‌وجو مشخص شوند. |
| آرشیو مکالمه | مسیرهای API گفتگو و history ورودی | ذخیره‌سازی canonical مکالمه از کد ممیزی‌شده اثبات نشده است | پیش از ساخت مخزن، رفتار فعلی و الزامات حریم خصوصی/حذف بررسی شود. |
| ایندکس بازیابی | جست‌وجوی lexical/vector | مخزن canonical یا ایندکس مستقل اثبات نشده است | ایندکس باید دادهٔ مشتق‌شده و بازسازی‌پذیر باشد؛ ایجاد DB تا اثبات نیاز به تعویق افتد. |
| گراف دانش | مسیرها و پیش‌نیازهای یادگیری | رابطه‌های ساختاری محدود وجود دارد؛ گراف عمومی مستقل اثبات نشده است | ابتدا نیازهای traversal و query اندازه‌گیری شود؛ تعداد DB از پیش ثابت نیست. |

## یافته‌های schema و مسیرهای داده

- `settings.db` فقط تنظیمات را نگه می‌دارد؛ `learning.db` داده‌های یادگیری و صف را نگه می‌دارد.
- schemaها در `app/schema_registry.py` به‌صورت migrationهای نسخه‌بندی‌شده تعریف شده‌اند و `db.init_databases()` و `task_manager.initialize_task_store()` از همان registry استفاده می‌کنند. قبل از baseline/ارتقا backup اعتبارسنجی‌شده تهیه می‌شود؛ schema ناشناخته و drift بیرونی خودکار پذیرفته یا تعمیر نمی‌شود.
- `learning.submit_job()` و شروع/تولید مجدد درس‌ها به task پایدار `learning.run_job` ارسال می‌شوند؛ payload صف فقط `job_id` دارد و متن منبع را کپی نمی‌کند.
- `learning_tasks.reconcile_queued_learning_tasks()` در startup برای رکوردهای queued/running، task متناظر را به‌صورت idempotent تضمین می‌کند.
- `task_manager.get_task_by_dedupe_key()` و آداپتر یادگیری، توقف از API عمومی صف را با وضعیت رکورد دامنه هماهنگ می‌کنند.
- هیچ داده‌ای به DB جدید منتقل نشده و تصمیمی برای ساخت دیتابیس جداگانهٔ تجربه، مکالمه، retrieval یا graph گرفته نشده است.

## ریسک‌های باقی‌مانده

1. ایجاد learning job و task دو عملیات جداست؛ startup reconciliation پنجرهٔ قطع فرایند را پوشش می‌دهد، اما fault-injection در همهٔ مرزهای commit هنوز لازم است.
2. migration runner، schema version metadata و ابزار backup/restore با آزمون بازیابی پیاده‌سازی و در CI پوشش داده شده‌اند؛ هنوز روی فایل‌های واقعی دیتابیس کاربر اجرا/اعتبارسنجی نشده‌اند.
3. وضعیت واقعی schema در فایل‌های DB کاربر بررسی نشده است؛ پیش از مهاجرت باید fingerprint و integrity check انجام شود.
4. توقف یک task در حال اجرای مدل الزاماً پردازش مدل را فوراً قطع نمی‌کند؛ نتیجهٔ لغوشده نباید در دانش canonical ذخیره شود.

## وضعیت پس از migration runner و startup

- `settings.db` و `learning.db` همچنان دو فایل مستقل‌اند؛ migrationهای نسخه‌بندی‌شده، backup قبل از adoption/upgrade، restore اتمیک با opt-in برای overwrite و رد schema ناشناخته پیاده‌سازی شده‌اند.
- `task_queue` فعلاً در `learning.db` باقی می‌ماند؛ صف و learning job به reconciliation/idempotency مشترک وابسته‌اند و جداکردن آن بدون قرارداد outbox یا همگام‌سازی و آزمون fault-injection ریسک تازه ایجاد می‌کند.
- migration fixtureها شناسه‌ها، source_ref، content_hash، متن منبع، knowledge item، assessment، review، مسیر یادگیری و task را بررسی می‌کنند. این آزمون‌ها مهاجرت schema را پوشش می‌دهند، نه انتقال فیزیکی میان دو فایل DB.
- برای archive مکالمه، audit مستقل، vector/lexical index و graph هیچ مخزن canonical اثبات نشده؛ تا زمانی که نیاز و مسیر داده مشخص نشود، دیتابیس جدید ساخته نمی‌شود.
- فایل‌های دیتابیس واقعی کاربر در این کار دست‌کاری نشده‌اند؛ پیش از هر انتقال واقعی باید backup، dry-run، شمارش/hash و restore روی کپی انجام شود.

## گام بعدی

تکمیل ماتریس مرز داده با تصمیم مستند برای هر حوزه و افزودن fault-injection در پنجره‌های crash میان ثبت learning job، enqueue، ذخیرهٔ نتیجه و تکمیل task. تفکیک فیزیکی جدید تنها زمانی انجام شود که مزیت تراکنشی/امنیتی/عملیاتی آن اثبات شود.
