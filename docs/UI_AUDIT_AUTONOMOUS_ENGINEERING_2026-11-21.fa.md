# ممیزی رابط کاربری و اولویت تکمیل ماژول مهندسی — 2026-11-21

## دامنه و مبنای بررسی

- مخزن: `cognitive-kernel/Personal-AI-assistant`
- شاخه و مبنای بررسی: `main` در commit `57971c0f7e59acfdc1fb29bd320f4aeebc868502`
- اولویت محصول: تکمیل ماژول **Autonomous Engineering** از UI تا API، مالکیت state، Runner، Codex، اعتبارسنجی، انتشار PR و بازیابی.
- این سند ممیزی ایستا و مبتنی بر فایل‌های مخزن است. در این نشست به مرورگر، PowerShell و سرویس‌های محلی رایانهٔ کاربر دسترسی اجرایی نداشتم؛ بنابراین هیچ ادعایی دربارهٔ اجرای زندهٔ UI یا تست روی میزبان کاربر مطرح نمی‌شود.

## جمع‌بندی اجرایی

ماژول مهندسی یک صفحهٔ مستقل `/autonomous-engineering`، چند فایل JavaScript تخصصی، API محلی، workflowهای GitHub Actions، state/checkpoint مشترک و کنترلر Runner دارد. پس مسئله صرفاً «وجود نداشتن UI» نیست؛ مسئلهٔ اصلی، اثبات نشدن کارکرد کامل جریان واقعی در محیط هدف و شکاف میان تست قرارداد/واحد، تست مرورگر و عملیات واقعی است.

مهم‌ترین گیت‌ها:

1. مالکیت state اجرا، قفل هم‌زمانی، fencing و recovery باید به‌عنوان یک قرارداد معماری واحد و قابل آزمون تثبیت شوند.
2. ارسال وظیفه و شروع چرخه باید خطای واقعی را با شناسهٔ correlation و جزئیات امن نشان دهد؛ پاسخ عمومی 502 بدون stderr/exit code برای تشخیص کافی نیست.
3. UI باید از وضعیت سرور مشتق شود؛ هیچ کنترل حساس نباید صرفاً بر پایهٔ snapshot قدیمی یا state محلی فعال شود.
4. Browser E2E باید مسیر کامل ماژول مهندسی را از بارگذاری صفحه تا dispatch، مشاهدهٔ اجرا، نمایش پیشرفت، pause/resume/cancel، شکست، retry و recovery پوشش دهد.
5. اجرای زنده روی Windows target با حساب واقعی Runner و Ollama/Codex و نیز پذیرش انسانی هنوز با تست‌های mocked یا CI جایگزین نمی‌شود.

## شواهد مستقیم از کد

### 1. صفحه و مرزهای UI

- صفحهٔ مستقل در `app/static/autonomous-engineering.html` قرار دارد.
- رفتار صفحه به فایل‌های جدا تقسیم شده است:
  - `autonomous-engineering-core.js`: قرارداد مشترک DOM/درخواست/پیام
  - `autonomous-engineering-status.js`: readiness، وضعیت و فعال/غیرفعال‌سازی کنترل‌ها
  - `autonomous-engineering-controls.js`: تنظیمات، dispatch، pause/resume/cancel
  - `autonomous-engineering-runs.js`: فهرست اجراها
  - `autonomous-engineering-progress.js`: progress و activity
  - `autonomous-engineering.js`: اتصال ماژول‌ها و refresh دوره‌ای
- این تفکیک اولیه مفید است؛ اما صرف وجود فایل‌های مجزا، استقلال واقعی را ثابت نمی‌کند. قراردادهای window-global، ترتیب بارگذاری scriptها و رفتار در صورت شکست/عدم بارگذاری هر ماژول باید با تست معماری و مرورگر اثبات شوند.
- `autonomous-engineering.js` در صورت نبودن core پیام خطا نشان می‌دهد؛ برای سایر بخش‌ها missing module را ثبت می‌کند. باید بررسی شود آیا شکست یک زیرماژول قابلیت‌های مستقل دیگر را واقعاً حفظ می‌کند و آیا صفحه می‌تواند بازیابی/تلاش مجدد کند.

### 2. مسیر dispatch و feedback خطا

- API در `app/autonomous_engineering.py` پیاده‌سازی شده و UI از endpointهای همین ماژول استفاده می‌کند.
- مسیر dispatch پیش‌شرط‌های readiness، دسترسی GitHub، وضعیت durable task/checkpoint، اجرای فعال و وضعیت PR را بررسی می‌کند.
- در نسخهٔ بررسی‌شده، شکست فرمان `gh workflow run` به پاسخ عمومی «درخواست اجرای Workflow به GitHub ارسال نشد» تبدیل می‌شود؛ جزئیات کافی مانند exit code، stderr پالایش‌شده و correlation ID برای پشتیبانی/عیب‌یابی در UI تضمین نشده است. این یک نقص observability است و باید بدون افشای token، URLهای محرمانه یا دادهٔ task رفع شود.
- قرارداد خطا باید برای تمام endpointها یک شکل ساختاریافته و versioned داشته باشد: `code`, `message`, `correlation_id`, `retryable`, `details_safe`. متن فنی حساس فقط در log امن سرور ثبت شود.

### 3. کنترل‌های وابسته به state

- `autonomous-engineering-status.js` کنترل‌های dispatch و task را با readiness، اجرای فعال، وضعیت pending work و checkpoint کنترل می‌کند.
- این منطق fail-closed است و برای جلوگیری از ارسال هم‌زمان، وضعیت‌های pending و checkpoint را در نظر می‌گیرد.
- ریسک بررسی: هر تغییر سرور بین pollهای ۳۰ ثانیه‌ای می‌تواند snapshot UI را کهنه کند. عملیات mutation باید پیش از ارسال از سرور authoritative validation بگیرد و بعد از mutation، state را دوباره از منبع اصلی بخواند. این رفتار باید با تست race و stale-response ثابت شود.
- polling وضعیت، runs و progress دوره‌ای است. باید درخواست‌های هم‌پوشان، پاسخ خارج از ترتیب، لغو صفحه، خطای شبکه و retry کنترل‌شده پوشش داده شوند؛ نه اینکه فقط متن status تغییر کند.

### 4. تست‌های فعلی و شکاف پذیرش

- `tests/test_autonomous_engineering.py` شامل تست‌های readiness، dispatch guard، route و قراردادهای ساختاری صفحه است؛ بخشی از UI assertions مبتنی بر وجود رشته/ID در فایل هستند و به‌تنهایی رفتار واقعی مرورگر را ثابت نمی‌کنند.
- `tests/test_browser_e2e.py` دارای تست مرورگر برای داشبورد و چند گردش‌کار ماژول‌هاست؛ در تست اصلی داشبورد، صفحهٔ خانه عمداً فقط چت را نشان می‌دهد و بخش‌های دیگر مخفی هستند. برخی صفحات از مسیر مستقل باز می‌شوند.
- سند `docs/UI_ACCEPTANCE.md` وضعیت تست‌های مرورگر و گیت‌های پذیرش دستی را ثبت می‌کند؛ بازبینی دستی keyboard/screen-reader، contrast و پذیرش روی میزبان هدف هنوز باید جداگانه تأیید شوند.
- مستند `docs/engineering/LEARNING_CANDIDATE_UI_2026-11-20.md` نمونهٔ واقعی از خطای UI است: یک عنصر متعلق به candidate به renderer وظایف منتقل شده و به متغیر تعریف‌نشدهٔ `item` ارجاع داده بود؛ در نتیجه refresh وظایف با ReferenceError متوقف می‌شد. این نمونه نشان می‌دهد تست‌های static/unit به‌تنهایی مانع regression بین ماژول‌های UI نیستند.
- در تست‌های فعلی، وجود تست E2E برای ماژول مهندسی به معنای پوشش end-to-end تمام رفتارهای عملیاتی آن نیست. inventory دقیق تست‌ها و مسیرهای فاقد coverage باید در ابتدای اجرای prompt تکمیل شود.

## یافته‌ها و اولویت‌ها

| شناسه | شدت | یافته | اقدام الزامی |
|---|---|---|---|
| UIE-01 | بحرانی | مالکیت state، lease/lock، fencing و recovery باید از UI تا Runner قرارداد واحد داشته باشند؛ مستند وضعیت پروژه هنوز گیت‌های معماری و میزبان هدف را باز ثبت کرده است. | بازبینی invariantها و تمام mutationها؛ تست race، stale owner، crash/restart و recovery؛ عدم حذف/بازنویسی task/checkpoint |
| UIE-02 | بالا | خطای dispatch در UI برای تشخیص عملیاتی به اندازهٔ کافی قابل مشاهده نیست. | خطای ساختاریافته، correlation ID، ثبت stderr امن و تست 4xx/5xx |
| UIE-03 | بالا | فعال‌بودن کنترل‌ها ممکن است به snapshot poll شده متکی باشد. | اعتبارسنجی server-side در هر mutation، مدیریت stale response و تست رقابت |
| UIE-04 | بالا | پوشش E2E عملیاتی کامل ماژول مهندسی از روی تست‌های موجود اثبات نشده است. | سناریوهای browser واقعی با backend آزمایشی و سپس پذیرش live روی target host |
| UIE-05 | بالا | تفکیک فایل‌های JS وجود دارد، اما استقلال runtime و fault isolation اثبات نشده است. | قرارداد نسخه‌دار، dependency graph، تست نبود/خطای هر زیرماژول و جلوگیری از global leakage |
| UIE-06 | متوسط | polling دوره‌ای برای status/runs/progress نیازمند کنترل race، timeout، backoff و نمایش خطا است. | single-flight، abort/sequence guard، backoff و تست پاسخ خارج از ترتیب |
| UIE-07 | بالا | تست‌های UI شامل موارد static-contract هستند که به تنهایی تجربهٔ کاربر را ثابت نمی‌کنند. | پوشش interaction، keyboard/accessibility، RTL/responsive و screenshot/trace در failure |

## ترتیب اجرای اجباری

1. Inventory و بازتولید: نقشهٔ صفحه → event handler → endpoint → service/state → Runner/workflow → GitHub evidence.
2. تثبیت معماری مالکیت state و execution lock؛ تعریف invariantها و تست‌های شکست قبل از refactor.
3. اصلاح observability و قرارداد خطا، به‌ویژه dispatch و Codex failure diagnostics.
4. اصلاح UI state machine، raceها و fault isolation.
5. افزودن E2E برای مسیرهای کامل و شکست‌ها.
6. اجرای CI exact-head.
7. اجرای پذیرش روی Windows target؛ ثبت خروجی واقعی PowerShell، مرورگر، Ollama، Codex و GitHub Actions.
8. فقط پس از عبور از gates مستندشده، PR آمادهٔ review شود. merge به main بدون عبور از گیت‌های target-host و exact-head ممنوع است.

## تعریف پذیرش ماژول مهندسی

ماژول فقط وقتی قابل استفاده اعلام می‌شود که همهٔ موارد زیر با شواهد قابل تکرار پاس شوند:

- صفحه در مرورگر هدف بدون uncaught exception و بدون شکست asset/module load باز شود.
- readiness هر dependency با وضعیت واقعی و علت قابل فهم نمایش داده شود.
- ذخیرهٔ تنظیمات، انتخاب مدل، validation و reload persistence درست کار کند.
- ارسال وظیفه، جلوگیری از duplicate dispatch و نمایش نتیجه/خطای دقیق امن کار کند.
- task و checkpoint در برابر refresh، restart، شکست worker و reclaim حفظ شوند.
- مالکیت lease/epoch مانع mutation توسط worker قدیمی شود.
- progress، activity و run history به دادهٔ همان run متصل و در برابر پاسخ‌های out-of-order مقاوم باشند.
- pause/resume/cancel فقط با state مجاز و با نتیجهٔ تأییدشده انجام شوند.
- Codex failure diagnostics واقعی و امن ثبت و به retry منتقل شوند؛ retry تکرار کورکورانه نباشد.
- PR و CI به SHA دقیق متصل باشند و merge خودکار رخ ندهد.
- Browser E2E، تست‌های واحد/یکپارچگی/هم‌زمانی/recovery و تست روی Windows target موفق باشند.
- UI فارسی RTL، صفحه‌کلید، focus، پیام خطا، حالت loading/empty/success و viewport موبایل/دسکتاپ بررسی شوند.

## محدودیت شواهد این ممیزی

این سند ادعای «تمام باگ‌های UI پیدا شده‌اند» ندارد. برای چنین ادعایی باید تمام صفحات و کنترل‌ها در مرورگر اجرا شوند و به سرویس واقعی/آزمایشی متصل شوند. این گزارش صرفاً یافته‌های قابل استناد از کد و مستندات موجود را ثبت می‌کند و نقشهٔ کار ممیزی زنده را مشخص می‌سازد.


## الحاقیهٔ راستی‌آزمایی وضعیت معماری — 2026-11-21

بررسی مجدد وضعیت زندهٔ GitHub و مستندات main یک نکتهٔ مهم را روشن کرد که باید پیش از هر تغییر اجرایی لحاظ شود:

- PR شمارهٔ [799](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/799) با عنوان `Fence durable task ownership with monotonic lease epochs` در تاریخ 2026-11-21 ادغام شده است؛ merge commit برابر `852423ae8b862b834e007eada0033d087a37cd83` است. بنابراین fencing مبتنی بر epoch و تفکیک task manager در main وجود دارد و نباید دوباره از صفر یا با تغییر موازی پیاده‌سازی شود.
- طبق `docs/PROJECT_STATUS.md`، PR #799 قرارداد lease/epoch، افزایش epoch هنگام claim، اعتبارسنجی owner+epoch+expiry و تست‌های worker قدیمی را اضافه کرده است. همان سند هنوز گیت‌های تکمیل اتصال fencing به تمام mutationهای دامنه‌ای و پذیرش زندهٔ Windows را باز می‌داند.
- در نتیجه، UIE-01 به معنی «فقدان fencing» نیست؛ معنای دقیق آن **نیاز به ممیزی پوشش end-to-end fencing و مالکیت execution در همهٔ مرزها** است. قبل از هر تغییر، باید commit ادغام‌شدهٔ #799 و تمام call siteهای آن بررسی شوند و فقط شکاف‌های اثبات‌شده اصلاح شوند.
- اجرای autonomous قبلی [run 44907149358](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44907149358) اکنون completed/failed است. لاگ job نشان می‌دهد دو تلاش Codex اجرا شده و خطای نهایی اعتبارسنجی گزارش بوده است: `Codex report must contain exactly one valid completion marker (found 0)`. task به‌عنوان verified منتشر نشده و طبق پیام کنترلر برای recovery نگه داشته شده است. این evidence باید در تحلیل قرارداد خروجی Codex و recovery لحاظ شود.
- PR مستنداتی #814 در حال گذراندن Full Windows test suite و تست اصلی است؛ تا پایان همهٔ checkها ادغام انجام نشده است.

**اثر بر برنامه:** Gate B باید با ممیزی کد موجود پس از PR #799 آغاز شود؛ ابتدا state transition و مسیرهای mutation را inventory کن، پوشش lease epoch و قفل‌ها را با تست اثبات کن، سپس تنها شکاف‌های باقی‌مانده را اصلاح کن. پیاده‌سازی تکراری fencing ممنوع است.
