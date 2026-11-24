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
- PR مستنداتی [#814](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/814) ادغام شده است؛ merge commit برابر `4eb6593a6beb70c7aa6423d8951cc3a88eeabb2d` است.

**اثر بر برنامه:** Gate B باید با ممیزی کد موجود پس از PR #799 آغاز شود؛ ابتدا state transition و مسیرهای mutation را inventory کن، پوشش lease epoch و قفل‌ها را با تست اثبات کن، سپس تنها شکاف‌های باقی‌مانده را اصلاح کن. پیاده‌سازی تکراری fencing ممنوع است.


## تکمیل پوشش تاب‌آوری رابط — 2026-11-21

- پوشش مرورگر موجود شامل بارگذاری صفحه و وضعیت محلی، ارسال وظیفهٔ محدودشده، انتخاب صریح مدل، قفل‌ماندن dispatch پس از پذیرش، بازیابی خطای لغو، گزارش اجرا/حذف/پاک‌سازی، تشخیص شکست، جلوگیری از اعمال پاسخ قدیمی progress/history، نمایش blockerهای پایدار و کنترل Pause/Resume است.
- دو آزمون رگرسیون اضافه شد: (۱) RTL و دسترسی صفحه‌کلید/Skip Link در عرض‌های 390، 768 و 1440 پیکسل با بررسی نبود overflow افقی؛ (۲) قطع عمدی فقط ماژول progress و اطمینان از ادامهٔ بارگذاری status، controls و runs بدون خطای uncaught.
- تغییر اولیه فقط آزمون بود؛ در ادامه فقط چرخهٔ نمایش هشدار خرابی ماژول اصلاح شد و مسیر ماژول‌های سالم دست‌نخورده ماند. نتیجهٔ E2E روی HEAD دقیق باید قبل از پذیرش ثبت شود.


### نقص کشف‌شده و اصلاح‌شده در پیام خطای ماژول

آزمون fault-isolation نشان داد که پیام شکست بارگذاری یک ماژول در `#agent-message` پس از پایان polling وضعیت بازنویسی می‌شد. پیام خطا اکنون در alert مستقل `#agent-module-warning` با `role=alert` و `aria-live=assertive` نمایش داده می‌شود؛ بنابراین polling وضعیت و پیام سلامت آن مستقل می‌مانند و هشدار خرابی ماژول پاک نمی‌شود.


### خطای ساختاریافته و شناسهٔ پیگیری در UI

ممیزی Gate C نشان داد helper مشترک درخواست، headerهای `X-Error-Code`، `X-Correlation-ID` و `X-Retryable` را دور می‌انداخت. همچنین handler اجرای بدون متن پیام خطا را قبل از refresh وضعیت می‌نوشت و refresh آن را بازنویسی می‌کرد. اکنون metadata امن خطا حفظ و در پیام نمایش داده می‌شود؛ dispatch/resume/manual-task از `retryable` برای حفظ قفل جلوگیری از ارسال تکراری استفاده می‌کنند و retry خودکار انجام نمی‌شود. refresh وضعیت پیش از نمایش پیام نهایی انجام می‌شود. آزمون Chromium خطای 503 را با code و correlation ID تزریق و نمایش پایدار آن را بررسی می‌کند.


## راستی‌آزمایی exact-head — PR #820 (2026-11-21)

- head کد `255f821639b9b5403abfc43c9ec0c889d812c836` تمام چهار workflow را با موفقیت گذراند: tests [44930929256](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44930929256)، browser E2E [44930929297](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44930929297)، CodeQL [44930929282](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44930929282)، dependency audit [44930929328](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/44930929328).
- Ubuntu: 2464 passed/14 skipped/74 deselected؛ Windows: 2462 passed/16 skipped/74 deselected؛ Chromium: 67 passed؛ Windows storage: 11 passed/2 skipped.
- PR #814 قبلاً ادغام شده است؛ وضعیت قدیمی «در حال گذراندن CI» در الحاقیهٔ قبلی با merge commit واقعی جایگزین شد.
- PR #819 در head `f7895430f26b6959bb42950cf8f61c418b3e0336` هنوز Full tests را می‌گذراند؛ موفقیت آن تا دریافت نتیجهٔ همان SHA ادعا نمی‌شود.
- این تغییرات فقط مستندات‌اند و برای head جدید PR #820 اجرای دوبارهٔ CI لازم است. پذیرش Windows target و بازبینی انسانی همچنان باز است.


### تکمیل polling در Gate D — 2026-11-22

ممیزی جدید نشان داد `setInterval`های ثابت برای status، runs و progress حتی هنگام hidden بودن صفحه ادامه می‌یافتند و پس از خطا backoff/jitter نداشتند؛ همچنین lifecycle صریح برای پاک‌سازی timerها وجود نداشت. در شاخهٔ follow-up:

- `autonomous-engineering-core.js` یک polling controller مشترک با single-flight، exponential backoff محدود، jitter محدود و حداکثر فاصلهٔ مشخص ارائه می‌کند.
- هنگام hidden شدن صفحه timer حذف می‌شود و با visible شدن refresh فوری انجام می‌شود؛ `stop()` و `pagehide` timer و listenerهای visibility را پاک می‌کنند و `pageshow` در بازگشت از bfcache polling را دوباره فعال می‌کند.
- refreshهای Status/Runs/Progress و monitor وضعیت دانلود مدل نتیجهٔ موفق/ناموفق را به polling controller بازمی‌گردانند تا backoff واقعی اعمال شود. خطای GET مدل حداکثر پنج بار متوالی با backoff محدود retry می‌شود و job دانلود سمت سرور لغو نمی‌شود. timer قفل visibility در Status و monitor مدل در teardown صریح پاک می‌شوند؛ timeout هر درخواست از helper مشترک حفظ شده است.
- رگرسیون Chromium زمان‌بندی hidden/visible، backoff، jitter و عدم اجرای refresh پس از cleanup را می‌سنجد. تا پایان exact-head CI این مورد پذیرفته‌شده محسوب نمی‌شود.


## 2026-11-24 addendum — Broker readiness root cause and explicit setup UX

### یافته‌های تأییدشده از کد

1. installer قبلی روی Windows مقصد به `csc.exe` وابسته بود؛ حذف این وابستگی مستلزم package واقعی بود، نه صرفاً حذف compile command.
2. draft prebuilt package به binary و manifestی ارجاع می‌داد که هنوز در repository نبودند. workflow Windows نیز یک بلوک ناقص و duplicate/orphaned job content داشت؛ بنابراین شواهد CI آن head معتبر نبود.
3. discovery اولیه فقط `powershell.exe` را می‌دید؛ نصب PowerShell Core-only را تشخیص نمی‌داد. UI نیز Runner را به‌اشتباه prerequisite نصب Broker نمایش می‌داد.
4. startup به‌صورت ضمنی UAC/نصب Broker را آغاز می‌کرد و `run.py` مسیرهای auto-install/start/pull برای Ollama داشت؛ این رفتار با اصل preflight خواندنی و اقدام privileged صریح ناسازگار بود.

### اصلاحات پیاده‌سازی‌شده در Draft PR #863

- Windows Broker از CI به‌صورت executable از پیش‌ساخته‌شده تولید می‌شود؛ target compiler/SDK لازم ندارد. SHA-256 package و staged copy پیش از service mutation بررسی می‌شود؛ runtime .NET Framework 4.8+ و ابزارهای service/ACL پیش از توقف Runner یا تغییر ACL preflight می‌شوند.
- workflow ساخت package بازسازی شد: package اولیه stage و upload می‌شود، provenance attestation صادر می‌شود، تست LocalService/named pipe/ACL اجرا می‌شود و job commit package به‌صورت idempotent است. exact-head دوم باید hash build تازه را با binary commitشده تطبیق دهد.
- Core و Desktop از PATH و registry کشف می‌شوند؛ انتخاب با capability واقعی است، نه major-version floor. همان executable برای UAC relaunch استفاده می‌شود.
- startup فقط health check خواندنی Broker و بررسی خواندنی Ollama/مدل‌ها انجام می‌دهد. نصب Broker فقط از دکمهٔ UI و endpoint loopback-protected انجام می‌شود. Ollama install/start/model pull خودکار حذف شده است.
- Broker مستقل از Runner است؛ نبود Runner فقط اجرای Workflow remote را غیرفعال می‌کند.
- setup endpoint خطاهای host missing، UAC cancel، timeout، install failure و post-install health failure را تفکیک می‌کند؛ setup lock بین‌پردازه‌ای OS از نصب هم‌زمان جلوگیری می‌کند.

### شواهد و محدودیت‌ها

- regressionهای unit/API/Browser برای startup read-only، setup action، lock process boundary، خطاها و refresh اضافه شده‌اند؛ این‌ها تا تکمیل exact-head CI صرفاً test definitions هستند، نه نتیجهٔ موفق.
- Windows PowerShell Desktop و Core در CI باید روی package واقعی تست شوند. نسخه‌ها در کد gate عددی ندارند؛ .NET Framework 4.8+ runtime و میزبان PowerShell دارای capabilityهای لازم هنوز پیش‌نیاز Windows Broker هستند.
- مسیر native/PowerShell-free برای service-control و ACL هنوز پیاده‌سازی نشده است.
- Browser E2E با API stubbed جایگزین اجرای واقعی UAC، سرویس LocalService، دو حساب غیرادمین، Runner حاضر/غایب، restart و checkpoint preservation روی Windows target نیست. Gateهای G/H/J همچنان pending هستند.
- هیچ task، checkpoint، database، ACL، Runner registration یا دادهٔ کاربر تغییر داده نشده است؛ PR Draft می‌ماند و merge مجاز نیست تا تمام gateها تأیید شوند.

## الحاقیهٔ ممیزی — 2026-11-24

- دو نقص UI/backend که پس از ممیزی اولیه آشکار شدند، در main اصلاح شده‌اند: refresh پس از mutation اکنون سریالی است و Cancel بر اساس وضعیت job واقعی Codex تصمیم می‌گیرد، نه صرفاً جدیدترین workflow run. exact-head CI هر دو PR #864 و #867 موفق است.
- در بازبینی مجدد، رویداد cleanup-completed در progress module می‌توانست هنگام busy بودن درخواست جاری refresh را drop کند. follow-up #870 آن را به صف coalesced منتقل می‌کند؛ این اصلاح هنوز exact-head CI نهایی ندارد.
- گیت‌های میزبان واقعی، از جمله Windows non-admin/UAC، Runner، Broker/ACL، Ollama/Codex و dispatch واقعی، همچنان NOT RUN هستند. بنابراین وضعیت repository CI نباید به‌عنوان پذیرش عملیاتی UI گزارش شود.



## الحاقیهٔ ممیزی — Cancel ambiguity، 2026-11-24

- بازبینی endpoint نشان داد انتخاب اولین queued candidate یا اولین active run در حالت چندتایی می‌تواند اجرای اشتباه را لغو کند؛ همچنین Cancel می‌توانست با Dispatch/Resume هم‌زمان اجرا شود.
- اصلاح فعلی lock مشترک را اعمال می‌کند و در چند Codex job فعال، چند candidate صف‌شده یا چند workflow بدون هدف یکتا fail-closed است. fallback فقط برای یک workflow فعال مجاز است.
- تست‌های regression اضافه شده‌اند؛ تا اجرای exact-head CI و Windows target-host، این اصلاح هنوز پذیرش نهایی ندارد.
