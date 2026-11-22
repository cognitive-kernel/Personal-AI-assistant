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
- PR #817 در head `722e007d1c8f56d2fd24081633eede556d30ef5f` برای routeهای API error envelope یکنواخت شامل code/message/correlation_id/retryable/safe_details و headerهای متناظر دارد؛ خطاهای validation و exceptionهای غیرمنتظره redacted می‌شوند. هر چهار workflow exact-head موفق‌اند؛ یافته‌های review دربارهٔ launch/timeout، resume retryability و unmatched route پس از بررسی کد و تست‌ها resolve شدند. PR هنوز unmerged است.

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

1. snapshot وضعیت GitHub در 2026-11-22: PRهای #817 و #818 روی headهای فعلی هر چهار workflow موفق دارند؛ head جدید PR #819 (`a0e247d08dabb6f3aa4ef3451b6fa8f885419524`) هنوز در انتظار چهار workflow است؛ کد PR #820 روی `3d9499574c347607ca08ee79080d19286cc19a99` هر چهار workflow را گذرانده، اما commit مستنداتی جدید `22ce582f815b3bffd3f8216623d82d6323546448` هنوز workflow تازه‌ای ندارد. این شواهد پذیرش target-host را جایگزین نمی‌کند.
2. تأیید و ادغام error envelope یکنواخت PR #817 پس از exact-head CI؛ سپس بررسی سازگاری همهٔ consumerها و حفظ قرارداد backward-compatible `detail`.
3. اجرای پذیرش واقعی روی Windows Runner service identity: Broker ACL/UAC، restart/crash، Pause/Resume/Cancel، checkpoint recovery، cleanup با/بدون active run و مشاهدهٔ log واقعی.
4. آزمون مدل pull در مرورگر mock شده است؛ آزمون عملی با Ollama محلی و مدل واقعی باید روی میزبان هدف انجام شود.
5. Gate B فراتر از writer اصلی `learning.run_job` هنوز باز است؛ candidate-review، curriculum، factual-verification و سایر domain mutation handlers باید جداگانه inventory و fence شوند. PR #819 فقط مسیر اصلی learning job و escalation را می‌بندد.
6. آزمون دسترس‌پذیری screen reader/contrast و مرور دستی keyboard traversal کامل هنوز نیازمند پذیرش مستقل است.

## 8. وضعیت تغییرات و مرز promotion

- مبنای main در زمان ممیزی: `91b7e062e21d06d774c2f1f394bc72e4eb69af2c`.
- PR #817: head فعلی `722e007d1c8f56d2fd24081633eede556d30ef5f`؛ هر چهار workflow exact-head موفق. review نهایی Codex بدون finding باز است و تمام threadهای inline پس از بررسی کد/تست‌ها resolve شده‌اند. PR باز و unmerged است.
- PR #818: head فعلی `e1fa88e9060cac16bf2061aac3eb7b0b73fc12dd`؛ بازیابی report نامعتبر/UTF-8 خراب فقط از پیام نهایی JSONL معتبر و turn موفق انجام می‌شود؛ هر چهار workflow exact-head موفق و review Codex بدون finding باز است. PR باز و unmerged است.
- PR #819: head قبلی `93d96cb3b2b61178c567e5b3d117402e0d234249` هر چهار workflow را گذرانده بود؛ head جدید `a0e247d08dabb6f3aa4ef3451b6fa8f885419524` در انتظار tests، browser-e2e، dependency-audit و CodeQL است. این فقط بخشی از Gate B است و سایر domain writers همچنان scope مستقل دارند.
- PR #820: کد head `3d9499574c347607ca08ee79080d19286cc19a99` چهار workflow موفق دارد؛ سپس فقط همین inventory در commit `22ce582f815b3bffd3f8216623d82d6323546448` به‌روزرسانی شد. این commit مستنداتی است و workflow تازه‌ای برای آن دیده نمی‌شود. PR همچنان Draft است؛ review Codex برای کد head قبلی در حال اجراست و نتیجه‌اش هنوز ثبت نشده است.
- هیچ‌یک از این PRها تا عبور exact-head CI و پذیرش میزبان هدف نباید صرفاً بر اساس کد/تست mock ادغام شوند.
- در این ممیزی task، checkpoint، تاریخچه، PR یا دادهٔ کاربر حذف یا بازنویسی نشده است.


### چرخهٔ پیام موفقیت پس از mutation

تمام کنترل‌های تغییردهندهٔ state (ذخیرهٔ تنظیمات، pull مدل، manual task، dispatch، Pause، Resume و Cancel) ابتدا وضعیت authoritative را بازخوانی و سپس پیام نهایی موفقیت را نمایش می‌دهند؛ بنابراین polling پیام نتیجه را بازنویسی نمی‌کند. در مسیر خطا نیز refresh قبل از نمایش خطا انجام می‌شود. E2E برای Settings، manual task، Pause/Resume و Cancel موفق/ناموفق این ترتیب را بررسی می‌کند.


## 9. مالکیت durable state و جدول انتقال

| انتقال | مالک مجاز | قفل/اعتبارسنجی | اثر پایدار | رفتار شکست |
|---|---|---|---|---|
| ثبت task دستی | workflow/controller تحت هویت Runner | `durable_state_lock` مشترک Python/PowerShell؛ اعتبارسنجی طول/محتوا؛ ثبت digest و owner metadata | فایل task و رکورد owner با run/attempt/source/claim_count به‌صورت atomic replace | در state ناخوانا، digest نامعتبر یا lock timeout هیچ task قدیمی بازنویسی نمی‌شود |
| admission و claim | schedule-gate سپس workflow | بررسی runهای active/queued؛ رد PR باز؛ زیر durable lock، reconcile و validate مالکیت task و digest | owner به run_id + run_attempt متصل می‌شود | اجرای تکراری سرکوب می‌شود؛ task pending حفظ می‌شود |
| اجرای worktree | همان run/attempt | `repository_execution_lock` مبتنی بر OS lock؛ قفل در مرگ process آزاد می‌شود | فقط یک controller مالک worktree است | اجرای دوم fail-closed؛ checkpoint و worktree دست‌نخورده |
| snapshot/checkpoint | watcher همان run/task | `checkpoint_store_lock`؛ watcher این قفل را در کل عمر نگه می‌دارد؛ metadata شامل base SHA، run/task و PID/start-time است | patch باینری، plan و metadata به‌صورت atomic؛ index فقط به checkpoint تأییدشده اشاره می‌کند | watcher دوم نمی‌تواند store را overwrite کند؛ خطای readiness به‌صورت marker مستقل ثبت می‌شود |
| recovery/restore | controller پس از گرفتن repository lock | توقف فقط process با PID + creation-time + command identity دقیق؛ سپس checkpoint lock؛ بررسی base SHA و patch byte count و `git apply --check --binary` | فقط patchی که دقیقاً روی base مجاز اعمال‌پذیر است restore می‌شود | هر ابهام یا mismatch باعث حفظ checkpoint و توقف recovery می‌شود |
| انتشار PR | controller همان run | repository lock در مسیر عادی run؛ durable lock هنگام ثبت pending PR؛ بررسی PR author/repository/branch/head/base | pending PR شامل number/url/head SHA/base SHA/branch/run/task، قبل از CI ذخیره می‌شود | تغییر head/base یا CI ناموفق task را pending نگه می‌دارد؛ merge خودکار وجود ندارد |
| پذیرش پس از merge | مالک انسانی + controller | merged_by باید User واقعی باشد؛ head/branch/url/base/merge SHA دقیق؛ main باید روی همان merge SHA بماند؛ post-merge tests/browser/audit | فقط پس از تأیید دوباره، checkpoint و pending state پاک می‌شوند | هر اختلافی مانع cleanup و dispatch بعدی می‌شود |
| cleanup Broker | Broker/service | durable-state lock و checkpoint-store lock؛ رد در صورت pending marker یا checkpoint فعال/تأییدنشده | فقط telemetry موقت حذف می‌شود؛ history، baseline، checkpoint و task/PR markers حفظ می‌شوند | conflict به UI برمی‌گردد؛ state پایدار پاک نمی‌شود |
| UI polling و mutation | UI فقط projection؛ API/workflow مرجع نهایی | server-side admission؛ sequence/single-flight برای پاسخ‌های قدیمی؛ retry خودکار dispatch ممنوع | UI فقط پیام و view را به‌روزرسانی می‌کند؛ مالک state نیست | stale response نباید کنترل/پیام جدیدتر را overwrite کند |

### شکاف‌های باقی‌ماندهٔ Gate B

1. PR #799 فقط queue-level owner/epoch را fence می‌کند. PR #819 مسیر اصلی `learning.run_job` و escalation را به transaction-level assertion متصل می‌کند؛ curriculum، candidate review، factual verification، research، sandbox، cloud discovery، programming و سایر domain writers هنوز باید جداگانه audit و fence شوند.
2. زیر‌دستور دستی `publish` در `scripts/autonomous_engineering_cycle.py` قبل از ورود به `repository_execution_lock` dispatch می‌شود. در workflow فعلی callsite ندارد و مسیر عادی `run` قفل را می‌گیرد؛ بااین‌حال اجرای دستی آن در زمان run هم‌زمان ایمن نیست. فایل controller در این مرحله محافظت‌شده است و تغییر داده نشده است.
3. قفل‌ها و تست‌های CI، پذیرش واقعی ACL هویت سرویس Windows، crash/restart، soak، Ollama/Codex و مرور انسانی را جایگزین نمی‌کنند.
