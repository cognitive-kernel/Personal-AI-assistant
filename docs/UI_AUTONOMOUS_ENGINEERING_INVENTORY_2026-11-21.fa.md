# فهرست مهندسی UI — Autonomous Engineering
تاریخ ممیزی: 2026-11-21  
مبنای کد: `main` در `91b7e062e21d06d774c2f1f394bc72e4eb69af2c`  
سند حاکم: `docs/MASTER_UI_ENGINEERING_PROMPT.fa.md`

## 1. مرز ماژول و گراف بارگذاری

صفحه: `/autonomous-engineering`  
API prefix: `/api/autonomous-engineering`

| ماژول | فایل | وابستگی | مسئولیت |
|---|---|---|---|
| Core | `autonomous-engineering-core.js` | ندارد | انتخاب DOM، پیام عمومی، fetch با timeout و خطای مشترک |
| Status | `autonomous-engineering-status.js` | Core | readiness، مدل‌ها، pending/checkpoint، مجازبودن dispatch و وضعیت Pause/Resume/Cancel |
| Controls | `autonomous-engineering-controls.js` | Core + Status | ذخیره تنظیمات، pull مدل، ارسال task، dispatch، pause/resume/cancel |
| Runs | `autonomous-engineering-runs.js` | Core | تاریخچه، گزارش و تشخیص خطا، حذف اجرای پایان‌یافته، cleanup |
| Progress | `autonomous-engineering-progress.js` | Core | پیشرفت جاری، نمودار scorecard، رویدادها و فیلتر تاریخ |
| Bootstrap | `autonomous-engineering.js` | همهٔ ماژول‌ها | بررسی نسخه/وجود ماژول‌ها و راه‌اندازی polling مستقل |

ماژول‌های مستقل Runs و Progress برای اجرا به Status وابسته نیستند. Controls عمداً بدون Status فعال نمی‌شود. شکست بارگذاری یک ماژول باید در alert مستقل `#agent-module-warning` بماند و پیام سلامت `#agent-message` را که Status به‌روزرسانی می‌کند بازنویسی نکند.

## 2. موجودی کنترل‌ها و قرارداد API

| کنترل / شناسه | رویداد و endpoint | ورودی/خروجی و انتقال state | قفل، retry و محدودیت |
|---|---|---|---|
| `#refresh-agent` | کلیک → `GET /status` | readiness، checks، model، pending_work، checkpoint، active run و دکمه‌ها را بازسازی می‌کند | درخواست status تک‌پروازی است؛ refresh اجباری پس از mutation منتظر درخواست قبلی می‌ماند |
| `#agent-settings-form` / `#save-agent-settings` | submit → `POST /settings` | `{model, interval_hours, execution_mode}`; مقادیر strict و پس از ذخیره در GitHub repository variables بازخوانی می‌شوند | دکمه تا پایان ذخیره غیرفعال؛ مدل باید نصب، قابل انتخاب، دارای metadata/context امن و پشتیبان tools باشد |
| `#pull-model` | کلیک با تأیید صریح → `POST /models/pull`؛ سپس polling از `GET /models/pull/{job_id}` | `{model}`; وضعیت queued/running/completed/failed و درصد دانلود در `#model-pull-status` | تنها یک دانلود فعال در سرویس؛ دکمه حین دانلود غیرفعال؛ تأیید حضور مدل در `/api/tags` قبل از اعلام موفقیت |
| `#agent-task-form` / `#send-agent-task` | submit → `POST /dispatch` | `{task}`; حداکثر 2000 نویسه و بدون NUL | فقط وقتی pending state در دسترس، unblocked و بدون checkpoint است؛ قفل dispatch برای جلوگیری از ارسال تکراری |
| `#dispatch-agent` | کلیک → `POST /dispatch` بدون body | اجرای بدون متن برای ادامه/تطبیق کار یا شروع چرخه؛ پاسخ فقط پذیرش درخواست را اعلام می‌کند، نه شروع قطعی Runner | readiness، pause، checkpoint، pending state، active run و PR باز بررسی می‌شوند؛ پس از پذیرش/نتیجه مبهم/5xx پنجرهٔ 30 ثانیه‌ای قفل |
| `#pause-agent` | کلیک → `POST /pause` | ثبت `AUTONOMOUS_ENGINEERING_PAUSED=true`; کار جاری تا مرز امن ادامه می‌یابد و task جدید شروع نمی‌شود | دکمه از وضعیت authoritative تعیین می‌شود؛ Pause معادل Cancel نیست |
| `#resume-agent` | کلیک → `POST /resume` | ثبت pause=false و dispatch چرخه برای ادامه از وضعیت پایدار | در خطای مبهم/قابل retry قفل dispatch حفظ می‌شود؛ بدون retry خودکار |
| `#cancel-agent` | کلیک و تأیید → `POST /cancel` | اجرای فعال GitHub Actions پیدا و cancel می‌شود | فقط وقتی اجرای فعال وجود دارد فعال است؛ Cancel با Pause متفاوت است |
| `#refresh-runs` | کلیک → `GET /runs` | فهرست اجراهای GitHub بازسازی می‌شود | sequence number مانع اعمال پاسخ قدیمی‌تر از refresh جدید می‌شود |
| دکمهٔ گزارش هر اجرا | کلیک → `GET /runs/{run_id}/logs` | خلاصه علت، مرحله شکست، diagnosticهای ثبت‌شده، job/stepهای ناموفق و log پالایش‌شده | run id مثبت؛ گزارش خام محدود و پالایش‌شده؛ برای اجرای فعال ممکن است log نهایی هنوز موجود نباشد |
| حذف اجرای قدیمی | تأیید → `DELETE /runs/{run_id}` | فقط اجرای `completed` از تاریخچه GitHub حذف می‌شود | اجرای فعال/صف‌شده با 409 حفظ می‌شود؛ پس از موفقیت تاریخچه refresh می‌شود |
| `#cleanup-agent-temp` | تأیید → `POST /cleanup` | فقط فایل‌های موقت progress/runtime پاک می‌شوند | اجرای فعال و blockerهای Broker مانع پاک‌سازی‌اند؛ history، checkpoint، task و PR پایدار حفظ می‌شوند |
| `#progress-period`, `#progress-start`, `#progress-end` | change → `GET /progress?period=...` | daily/weekly/monthly یا custom با start_date/end_date؛ نمودار، current phase، scorecard و events | تاریخ شروع نباید بعد از پایان باشد؛ پاسخ مربوط به انتخاب قبلی اعمال نمی‌شود و refresh جدید صف می‌شود |
| `#refresh-progress` | کلیک → همان `GET /progress` | بازخوانی progress برای بازه انتخاب‌شده | یک refresh هم‌زمان؛ درخواست جدید در صورت busy بودن صف می‌شود |
| لینک‌های GitHub | navigation خارجی | بازکردن Workflow/Run در GitHub | فقط URLهای HTTPS معتبر GitHub در تاریخچه نمایش داده می‌شوند |

## 3. Schemaهای درخواست

- `EngineeringSettings`: `model: StrictStr`, `interval_hours: StrictInt`, `execution_mode: StrictStr`؛ حالت‌های مجاز `scheduled` و `continuous`؛ فاصله‌های مجاز 1، 2، 4، 6، 12 یا 24 ساعت.
- `ManualTaskRequest`: `task: StrictStr` با سقف 2000 نویسه؛ NUL رد می‌شود.
- `ModelPullRequest`: `model: StrictStr` با طول 1 تا 120 و الگوی شناسه مدل.
- `GET /runs`: `{runs, repository, workflow}`.
- `GET /runs/{id}/logs`: `{run, failures, error_summary, failure_stage, diagnostic, diagnostics_available, logs, truncated}`.
- `GET /progress`: `{current, history, latest_snapshot, events, storage_configured}` به‌علاوهٔ اطلاعات پیشرفت غنی‌شده از GitHub.

## 4. State machine و مرز ایمنی

### 4.1 مجازبودن ارسال

`canStartDispatch = statusKnown && lastCanDispatch && !lastActiveRun && !dispatchSubmitting && now >= dispatchPendingUntil`.

ارسال task دستی شرط اضافه دارد: `pending_work.available === true`، `blocked !== true` و checkpoint موجود نباشد. checkpoint تأییدنشده دکمهٔ dispatch را نیز مسدود می‌کند.

### 4.2 جلوگیری از dispatch تکراری

- UI دکمه‌ها را حین ارسال غیرفعال و پس از پذیرش/نتیجه مبهم/5xx برای 30 ثانیه قفل می‌کند.
- API قبل از ارسال، readiness، pause، pending/checkpoint، active GitHub run و PR باز را بررسی می‌کند.
- Workflow در `schedule-gate` دوباره active/queued runها را بررسی می‌کند و در صورت مشاهده اجرای دیگر trigger تکراری را suppress می‌کند.
- Job اصلی دارای concurrency group ثابت با `cancel-in-progress: false` است.
- ثبت task دستی روی Runner زیر `durable_state_lock` انجام می‌شود و مالکیت آن قبل از اجرا اعتبارسنجی می‌شود.

### 4.3 Pause / Resume / Cancel

Pause یک متغیر پایدار GitHub است و توقف در مرز امن را درخواست می‌کند؛ Cancel اجرای جاری GitHub را لغو می‌کند؛ Resume متغیر را پاک و چرخه را دوباره dispatch می‌کند. UI فقط وضعیت برگشتی از `GET /status` را مبنای فعال/غیرفعال‌بودن دکمه‌ها قرار می‌دهد.

### 4.4 پاک‌سازی

API ابتدا active run را رد می‌کند و سپس Broker/state markers را بررسی می‌کند. حذف history، checkpoint، pending task، owner metadata یا pending PR جزو cleanup مجاز نیست.

## 5. Polling، پاسخ قدیمی و خطا

- Core timeout: status برابر 40 ثانیه، progress برابر 20 ثانیه، سایر درخواست‌ها 120 ثانیه. Polling دوره‌ای Status و Runs هر 30 ثانیه و Progress هر 5 ثانیه انجام می‌شود.
- Status: single-flight؛ refresh بعد از mutation منتظر درخواست قبلی می‌ماند و سپس snapshot authoritative می‌گیرد.
- Runs: sequence counter؛ پاسخ درخواست قدیمی دور انداخته می‌شود.
- Progress: انتخاب period/date در زمان پاسخ دوباره اعتبارسنجی می‌شود؛ درخواست متناظر با انتخاب جدید صف می‌شود.
- Model pull: polling هر 1.5 ثانیه تا completed/failed.
- Core اکنون `code`, `correlation_id`, `retryable`, `safe_details` را از JSON و headerهای `X-Error-Code`, `X-Correlation-ID`, `X-Retryable` می‌خواند و code/correlation امن را به پیام UI می‌افزاید. retry خودکار انجام نمی‌شود.
- PR #817 در شاخهٔ بررسی، برای تمام routeهای API یک error envelope یکنواخت شامل code/message/correlation_id/retryable/safe_details و headerهای متناظر اضافه می‌کند؛ خطاهای validation و exceptionهای غیرمنتظره نیز بدون echo ورودی یا جزئیات خام redacted می‌شوند. این تغییر تا عبور exact-head CI هنوز پذیرفته‌شده محسوب نمی‌شود.

## 6. پوشش تست موجود

### Browser E2E موجود در main

- render صفحه و local status؛
- ارسال manual task با سقف و payload دقیق؛
- انتخاب صریح مدل و ذخیرهٔ مدل درست؛
- قفل dispatch پس از پذیرش؛
- بازیابی دکمهٔ Cancel پس از پاسخ ردشده؛
- نمایش log/diagnostic، حذف run پایان‌یافته و cleanup؛
- نمایش failure diagnostic؛
- دورریختن پاسخ قدیمی progress و runs؛
- توضیح blockerهای durable task/checkpoint؛
- کنترل Pause/Resume بر اساس وضعیت سرور.

### Browser E2E افزوده‌شده در PR #820

- RTL، Skip Link با صفحه‌کلید و نبود overflow افقی در عرض‌های 390، 768 و 1440؛
- قطع عمدی progress module و اثبات ادامهٔ کار Status/Controls/Runs همراه هشدار پایدار؛
- خطای dispatch با 503 و بررسی code/correlation ID و قفل retry؛
- pull مدل با تأیید کاربر، polling پیشرفت، نمایش خطای ساختاریافته و بازیابی دکمه.


- مسیر موفق و شکست model pull هر دو در E2E مرورگر پوشش داده شده‌اند؛ خطای موقت Ollama باید code/correlation را نشان دهد و کنترل را دوباره فعال کند.

### Unit/API

`tests/test_autonomous_engineering_model_pull.py` تست loopback Ollama، پیشرفت، inventory verification، خطا، admission تک‌دانلود و endpointهای start/status را پوشش می‌دهد. `tests/test_autonomous_engineering_dispatch_safety.py` و تست‌های state lock/pending task، gateهای backend و durable state را پوشش می‌دهند.

## 7. موارد باز برای تکمیل و پذیرش

1. اجرای exact-head CI برای PRهای #817–#820 و رفع هر شکست پیش از promotion.
2. تأیید و ادغام error envelope یکنواخت PR #817 پس از exact-head CI؛ سپس بررسی سازگاری همهٔ consumerها و حفظ قرارداد backward-compatible `detail`.
3. اجرای پذیرش واقعی روی Windows Runner service identity: Broker ACL/UAC، restart/crash، Pause/Resume/Cancel، checkpoint recovery، cleanup با/بدون active run و مشاهدهٔ log واقعی.
4. آزمون مدل pull در مرورگر mock شده است؛ آزمون عملی با Ollama محلی و مدل واقعی باید روی میزبان هدف انجام شود.
5. Gate B فراتر از writer اصلی `learning.run_job` هنوز باز است؛ candidate-review، curriculum، factual-verification و سایر domain mutation handlers باید جداگانه inventory و fence شوند. PR #819 فقط مسیر اصلی learning job و escalation را می‌بندد.
6. آزمون دسترس‌پذیری screen reader/contrast و مرور دستی keyboard traversal کامل هنوز نیازمند پذیرش مستقل است.

## 8. وضعیت تغییرات و مرز promotion

- مبنای main در زمان ممیزی: `91b7e062e21d06d774c2f1f394bc72e4eb69af2c`.
- PR #817: تشخیص قطعی launch failure در برابر timeout مبهم و correlation diagnostics؛ یک failure در mock تست کشف و اصلاح شده و CI جدید لازم است.
- PR #818: بازیابی Codex report از JSONL در صورت UTF-8 خراب؛ exact-head CI لازم است.
- PR #819: fencing تراکنش‌های اصلی learning job و escalation با lease epoch؛ draft و CI لازم است.
- PR #820: خطای پایدار ماژول، error metadata، dispatch message ordering، responsive/fault-isolation/model-pull E2E؛ draft و CI لازم است.
- هیچ‌یک از این PRها تا عبور exact-head CI و پذیرش میزبان هدف نباید صرفاً بر اساس کد/تست mock ادغام شوند.
- در این ممیزی task، checkpoint، تاریخچه، PR یا دادهٔ کاربر حذف یا بازنویسی نشده است.


### چرخهٔ پیام موفقیت پس از mutation

تمام کنترل‌های تغییردهندهٔ state (ذخیرهٔ تنظیمات، pull مدل، manual task، dispatch، Pause، Resume و Cancel) ابتدا وضعیت authoritative را بازخوانی و سپس پیام نهایی موفقیت را نمایش می‌دهند؛ بنابراین polling پیام نتیجه را بازنویسی نمی‌کند. در مسیر خطا نیز refresh قبل از نمایش خطا انجام می‌شود. E2E برای Settings، manual task، Pause/Resume و Cancel موفق/ناموفق این ترتیب را بررسی می‌کند.
