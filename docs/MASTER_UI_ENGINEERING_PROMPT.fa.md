# پرامپت اجرایی جامع — تکمیل ماژول Autonomous Engineering و رفع ریشه‌ای مشکلات UI

> این سند ادامهٔ الزامی `docs/MASTER_ENGINEERING_PROMPT.md` است، نه جایگزین آن. تمام محدودیت‌های امنیتی، تست، شواهد، عدم regression و ممنوعیت merge بدون تأیید گیت‌های آن سند برقرار می‌مانند. این سند اولویت اجرایی را مشخص می‌کند: ابتدا ماژول مهندسی خودکار از UI تا عملیات واقعی باید قابل استفاده شود؛ سپس همین روش برای سایر ماژول‌ها اجرا شود.

## 0. مأموریت و معیار موفقیت

تو مهندس ارشد نرم‌افزار، معمار سیستم، مهندس قابلیت اطمینان، مهندس UI و مهندس سیستم‌های هوش مصنوعی در مخزن `cognitive-kernel/Personal-AI-assistant` هستی.

هدف تولید commit یا سبز کردن صرف pytest نیست. هدف تحویل قابلیت عملیاتی است که کاربر بتواند از UI فارسی استفاده کند و نتیجهٔ واقعی، قابل مشاهده، امن و پایدار بگیرد.

**اولین و بالاترین اولویت: ماژول Autonomous Engineering در مسیر `/autonomous-engineering`.** تا وقتی این ماژول از مرورگر تا Runner/Codex/GitHub به‌صورت end-to-end کار نمی‌کند، سراغ توسعهٔ قابلیت جدید در ماژول‌های دیگر نرو. اصلاح regressionهای سایر قسمت‌ها که در اثر تغییرات تو ایجاد شده‌اند الزامی است.

موفقیت یعنی کاربر از UI بتواند وضعیت واقعی را ببیند، تنظیمات را ذخیره کند، وظیفه بفرستد، چرخه را اجرا کند، پیشرفت را دنبال کند، pause/resume/cancel مجاز را انجام دهد، خطا را با شناسه و علت امن بفهمد، و پس از restart/recovery داده و وظیفه‌اش را از دست ندهد. تست‌های واحد به‌تنهایی معیار موفقیت نیستند.

## 1. اسناد و منابع الزامی قبل از تغییر کد

ابتدا این اسناد را کامل بخوان و با وضعیت واقعی کد تطبیق بده:

1. `docs/MASTER_ENGINEERING_PROMPT.md` — قرارداد اصلی و غیرقابل تضعیف.
2. `docs/PROJECT_STATUS.md` — backlog، گیت‌های باز، وضعیت PRها و ادعاهای تاریخی.
3. `docs/UI_ACCEPTANCE.md` و `docs/UI_AUDIT_2026-10-30.md` — پوشش UI و گیت‌های پذیرش.
4. `docs/operations/CODEX_AUTONOMOUS_ENGINEERING.md` — معماری عملیاتی Runner/Ollama/Codex.
5. `docs/architecture/*` و ADRهای مرتبط با task ownership، lock، lease، fencing، broker، checkpoint و recovery.
6. `docs/ENGINEERING_LOG.md` و Issueهای باز مرتبط با UI و پذیرش کل پروژه.
7. گزارش `docs/UI_AUDIT_AUTONOMOUS_ENGINEERING_2026-11-21.fa.md` — یافته‌های اولیه و محدودیت شواهد.
8. همین پرامپت.

هیچ گزارش قدیمی را بدون بررسی SHA و تاریخ، وضعیت جاری فرض نکن. هر مورد را با main فعلی، PRهای باز و runهای واقعی تطبیق بده. اگر دو سند تعارض دارند، تعارض را ثبت و از وضعیت زندهٔ کد/API/Actions evidence استفاده کن.

## 2. اصول معماری غیرقابل مذاکره

### 2.1 مالکیت و دوام state — اولویت صفر

قبل از تغییر UI یا اضافه کردن قابلیت، معماری مالکیت وضعیت اجرا را بازبینی و کامل کن:

- یک مرجع authoritative برای هر اجرای workflow/task مشخص باشد.
- مالکیت task، lease، fencing epoch/token، execution lock، process ownership و checkpoint دارای invariantهای صریح و قابل آزمون باشند.
- worker قدیمی، process orphan، retry هم‌زمان یا run تکراری نتواند state را mutate کند.
- claim/reclaim/heartbeat/complete/fail/cancel transitions اتمیک، idempotent و قابل audit باشند.
- UI هرگز مالک حقیقت state نباشد؛ فقط projection از API باشد.
- broker، app process، Runner و workflow قرارداد نسخه‌دار و مستقل داشته باشند.
- restart برنامه، crash worker، قطع شبکه، timeout، runner restart و اجرای هم‌زمان، task/checkpoint را حذف یا خراب نکنند.
- task/checkpoint/PR ownership هرگز با فایل یا marker قدیمی و مبهم overwrite نشود.
- هر mutation با owner + fencing token/epoch معتبر و current lease مجاز باشد.
- قفل‌ها باید cross-process و در Windows target قابل اثبات باشند؛ thread lock به‌تنهایی کافی نیست.
- recovery فقط پس از تشخیص نوع شکست و تأیید نبود owner زنده/فعال انجام شود. شکست preflight، admission، UI یا CI نباید به‌اشتباه failure اجرای Codex شمرده شود.

قبل از refactor، همهٔ مسیرهای mutation و call siteها را پیدا کن و یک state transition table بساز. برای هر transition: precondition، owner، transaction/lock، postcondition، retry semantics و audit event ثبت شود.

### 2.2 معماری ماژولار واقعی

- هر فایل یک مسئولیت واحد و قابل توضیح داشته باشد.
- از فایل‌های بسیار حجیم و God Module جلوگیری کن؛ اما کد را صرفاً برای کم کردن تعداد خطوط به فایل‌های خرد و پراکنده تقسیم نکن.
- مرزها بر اساس capability/domain باشند: UI shell، UI feature controller، API contract/schema، application service، domain policy، persistence adapter، external provider adapter، workflow/runner adapter.
- وابستگی‌ها یک‌طرفه و صریح باشند؛ domain به FastAPI، DOM، GitHub CLI یا provider خاص وابسته نشود.
- از global mutable state و window-globalهای بدون قرارداد نسخه‌دار اجتناب کن.
- ارتباط بین ماژول‌ها از طریق interface/schema/event مشخص باشد.
- هر ماژول تست، fixture، مستند و migration/asset مورد نیاز خودش را در ساختار قابل کشف داشته باشد؛ تست‌های مشترک integration در لایهٔ مناسب بمانند.
- تغییر در یک ماژول نباید import، route، selector، CSS، storage یا رفتار ماژول دیگر را ناخواسته تغییر دهد.
- dependency graph و معماری را با fitness tests enforce کن.

### 2.3 قراردادها و خطاها

- تمام APIها request/response schema صریح، strict validation و versioning سازگار داشته باشند.
- خطاها ساختاریافته باشند: `code`, `message`, `correlation_id`, `retryable`, `safe_details`.
- متن کاربرپسند از diagnostic فنی جدا باشد.
- stderr/exception خام، token، secret، متن task خصوصی، مسیرهای حساس و credential هرگز به UI یا GitHub event/log ناامن نشت نکند.
- diagnostic امن باید علت، component، operation، exit code، نوع exception، timestamp، run/job/step IDs و correlation ID را نگه دارد.
- هیچ خطایی با catch خالی، success کاذب، fallback مبهم یا status سبز پنهان نشود.
- شکست یک feature نباید صفحه یا feature مستقل دیگر را از کار بیندازد.

## 3. فاز صفر — تثبیت وضعیت واقعی و جلوگیری از تخریب

1. SHA دقیق main، branchها، PRهای باز، CI runها، issueها و workflowهای active را ثبت کن.
2. هیچ run فعال، task، checkpoint، PR یا branch را بدون مجوز حذف/لغو/بازنویسی نکن.
3. اگر run فعال وجود دارد، مالکیت آن را شناسایی و فقط read-only بررسی کن؛ هرگز برای شروع task جدید state آن را دور نزن.
4. وضعیت pause، pending task، pending PR، cleanup و broker availability را قبل و بعد هر عملیات ثبت کن.
5. از داده‌های واقعی و DBهای کاربر فقط با backup/restore تأییدشده و مسیر non-destructive استفاده کن.
6. گزارش وضعیت اولیه را در audit doc و engineering log ثبت کن.

## 4. فاز یک — ممیزی کامل UI ماژول مهندسی

صفحه و تمام وابستگی‌های زیر را از کد تا رفتار مرورگر بررسی کن:

- `app/static/autonomous-engineering.html`
- `app/static/autonomous-engineering.css`
- `app/static/autonomous-engineering-core.js`
- `app/static/autonomous-engineering-status.js`
- `app/static/autonomous-engineering-controls.js`
- `app/static/autonomous-engineering-runs.js`
- `app/static/autonomous-engineering-progress.js`
- `app/static/autonomous-engineering.js`
- `app/autonomous_engineering*.py`
- state broker، task manager، checkpoint/process supervisor و scripts/workflows مرتبط
- `tests/test_autonomous_engineering*.py`
- `tests/test_browser_e2e.py`

برای هر control در HTML بساز:
- selector/ID و مقصد آن
- event handler
- endpoint + HTTP method
- schema ورودی/خروجی
- service/state transition
- حالت loading/disabled/success/error
- رفتار retry و idempotency
- تست واحد، integration و browser مربوط
- نتیجهٔ اجرای واقعی در مرورگر

هیچ دکمه یا فرم بدون event handler معتبر، endpoint قابل دسترس، پیام نتیجه و test نماند. کنترل غیرفعال باید علت روشن و قابل اقدام داشته باشد. اگر قابلیتی عمداً unavailable است، آن را صریح نمایش بده؛ ظاهر فعال با عملیات بی‌اثر ممنوع.

### گردش‌کارهای اجباری مرورگر

با Playwright در Chromium (و در صورت امکان Firefox/WebKit) هر کدام را اجرا و trace/screenshot/console/network failure را در artifact نگه دار:

1. بازکردن مستقیم `/autonomous-engineering` و refresh/reload.
2. بارگذاری تمام assetها و module dependencyها؛ خطای 404، MIME، syntax و uncaught exception.
3. دریافت readiness واقعی Ollama، مدل، Codex، Runner، GitHub CLI و broker.
4. انتخاب مدل نصب‌شده، مدل ناموجود، مدل context variant و ذخیرهٔ تنظیمات.
5. pull model با consent و progress/error.
6. dispatch چرخهٔ بدون task و task دستی معتبر/نامعتبر/طولانی.
7. double click، refresh حین dispatch، دو tab و concurrent dispatch.
8. پاسخ‌های 400/401/403/409/422/429/500/502/503، timeout و قطع شبکه.
9. اجرای فعال، queued، waiting، completed، failed و cancelled.
10. progress eventهای متوالی، ناقص، تکراری و out-of-order.
11. pause/resume/cancel با state مجاز و غیرمجاز.
12. run history، pagination/empty/error، لینک GitHub و stale run.
13. task/checkpoint موجود، verified/unverified، pending PR، cleanup در حال انجام.
14. Codex exit 1، timeout، Ollama unavailable، GitHub CLI failure و CI failure.
15. refresh/restart و recovery بدون حذف داده یا task.
16. RTL، متن فارسی/انگلیسی، keyboard-only، focus، aria-live، contrast و mobile/desktop layout.

تست E2E نباید همهٔ backend را mock کند و سپس نتیجه را «قابل استفاده» اعلام کند. دو لایه لازم است:
- E2E deterministic با سرویس‌های تستی برای تمام branchها و fault injection.
- پذیرش target-host با سرویس واقعی Windows Runner/Ollama/Codex/GitHub برای end-to-end عملیاتی.

## 5. فاز دو — اصلاح ریشه‌ای مشکلات ماژول مهندسی

### 5.1 Dispatch و خطای UI

- همهٔ gateها و status codeها را به error code ثابت و قابل ترجمه تبدیل کن.
- خروجی امن `gh workflow run` شامل exit code و stderr پالایش‌شده را در server diagnostic ثبت کن.
- correlation ID از UI request تا server log، workflow dispatch، run، job و task منتقل شود.
- UI خطای مشخص و اقدام بعدی را نمایش دهد؛ پیام عمومی مبهم ممنوع.
- برای خطاهای قابل retry، backoff و idempotency key داشته باش؛ برای conflict، state واقعی و لینک run فعال را نمایش بده.
- duplicate dispatch باید در backend و در storage/lock مهار شود، نه فقط با disabled button.
- regression test برای تمام branchهای خطا اضافه کن.

### 5.2 State و synchronization

- single source of truth برای run/task/checkpoint تعیین کن.
- state machine صریح و schema-versioned بساز.
- lock/lease/fencing تمام mutationها را پوشش دهد.
- stale UI request یا worker هیچ‌گاه state جدیدتر را overwrite نکند.
- transaction boundary و crash windowها را مستند کن.
- race test چند process واقعی و Windows test اضافه کن.
- recovery را با crash injection قبل/بعد هر durable write آزمایش کن.

### 5.3 Codex diagnostics و retry

- JSONL parser باید protocol version، event type، item ID، tool start/end، agent message، usage و error را validate کند.
- تمام failure details لازم برای عیب‌یابی به‌صورت امن و محدود persist شوند.
- retry prompt باید diagnostic واقعی و attempt history مرتبط را بگیرد؛ از ارسال secret یا کل log خام خودداری کن.
- خطاهای permanent، transient، admission، infrastructure، task failure و validation failure تفکیک شوند.
- retry count، total deadline، per-attempt timeout و circuit breaker شفاف و قابل تست باشند.
- پس از شکست، task/checkpoint و شواهد حفظ شوند و هیچ وضعیت success کاذب ثبت نشود.
- process tree و descendants باید پیش از retry کاملاً drain شوند.

### 5.4 UI fault isolation

- UI shell نباید تمام منطق feature را در یک فایل داشته باشد.
- هر feature module قرارداد versioned و initialization/teardown مستقل داشته باشد.
- خرابی runs panel نباید controls یا progress را از کار بیندازد؛ خرابی progress نباید status/dispatch را متوقف کند.
- هر ماژول باید error boundary عملی، retry دستی و status محلی داشته باشد.
- event listenerها idempotent bind شوند و در reload/navigation دوباره ثبت نشوند.
- درخواست‌های هم‌پوشان single-flight یا sequence guarded باشند؛ پاسخ قدیمی نباید DOM را overwrite کند.
- polling دارای timeout، backoff، jitter، visibility awareness و cleanup باشد.
- هیچ innerHTML با دادهٔ untrusted استفاده نشود؛ از textContent و schema validation استفاده کن.
- CSS هر feature تا حد ممکن scoped باشد؛ selectors عمومی نباید feature دیگر را خراب کنند.

## 6. فاز سه — اجرای تست معماری و regression

حداقل موارد زیر را اضافه/تقویت کن:

- unit tests برای state machine، schema، error mapping و UI controllers.
- API integration tests برای هر endpoint و همهٔ error branches.
- architecture fitness tests برای import boundaries، dependency direction، اندازهٔ فایل‌ها به‌عنوان هشدار قابل بازبینی (نه حد عددی کور)، ممنوعیت cyclic imports و منع global mutable state.
- contract tests برای UI/API/Runner/Broker/GitHub workflow.
- concurrency/race tests با چند process، stale owner و fencing epoch.
- crash/restart/recovery tests در نقاط مختلف persistence.
- Playwright E2E برای همهٔ گردش‌کارهای بخش 4.
- security tests برای loopback-only، auth، CSRF، secret redaction، path confinement، sandbox و عدم نشت task.
- accessibility/RTL/responsive tests.
- tests باید رفتار واقعی را اثبات کنند؛ تست brittle مبتنی بر صرفاً جست‌وجوی متن، به‌عنوان تنها پوشش پذیرفته نیست.
- تست قبلی را حذف، skip یا تضعیف نکن تا CI سبز شود.

## 7. فاز چهار — روش تغییر کد و modularity

برای هر defect:
1. reproduction دقیق با test failing.
2. root cause و 5 Whys مبتنی بر evidence.
3. blast radius و تمام call sites.
4. minimal correct fix یا refactor مرزبندی‌شده.
5. regression test که پیش از fix fail شود و پس از fix pass.
6. full relevant test suite.
7. مرور diff برای حفظ رفتارهای سالم.
8. مستندات/ADR و engineering log.

ساختار پیشنهادی را به زور تحمیل نکن؛ از مرزهای domain موجود استفاده کن. فایل‌های حجیم را به چند فایل مسئولیت‌محور تقسیم کن. هر فایل جدید باید دلیل، مالکیت، interface و تست مستقل داشته باشد. از انتقال کد به فایل‌های کوچک اما با coupling بیشتر خودداری کن.

هیچ تغییر بزرگی را در یک commit انباشته نکن. تغییرات معماری، backend، UI، تست و مستندات در commitهای منطقی و قابل revert باشند.

## 8. فاز پنج — اعتبارسنجی و انتشار

- یک branch اختصاصی از آخرین main بساز.
- تغییرات را به Draft PR کوچک و قابل review تقسیم کن، اما PRها باید ترتیب dependency روشن داشته باشند.
- تست‌ها را روی exact head SHA اجرا کن و لینک runها را ثبت کن.
- CI سبز بدون تست target host به معنی «قابل استفاده» نیست.
- پس از CI، روی Windows target:
  - PowerShell test suite کامل
  - Playwright روی برنامهٔ واقعی
  - سرویس Runner با هویت واقعی و ACLهای محدود
  - Ollama model و context runtime واقعی
  - Codex real tool call و یک task کنترل‌شدهٔ غیرمخرب
  - dispatch، progress، PR creation، exact-SHA CI، pause/resume/recovery
  - restart/crash و اثبات حفظ task/checkpoint
- خروجی خام تست، timestamp، commit SHA، run/job ID و artifact را ثبت کن؛ secretها را redact کن.
- تا وقتی تست‌های target host توسط مالک تأیید نشده‌اند، PR را merge نکن. branchهای قبلی، داده‌ها یا PRهای باز را بدون بررسی و مجوز حذف نکن.

## 9. ترتیب تحویل و Definition of Done

ماژول مهندسی به ترتیب این gates را می‌بندد:

- [ ] Gate A — inventory و reproduction تمام controls، APIها و state transitions
- [ ] Gate B — state ownership، lease/fencing، lock و recovery قابل اثبات
- [ ] Gate C — خطاهای ساختاریافته، correlation ID و Codex diagnostics قابل استفاده
- [ ] Gate D — UI state machine، fault isolation، stale request و polling قابل اتکا
- [ ] Gate E — Playwright end-to-end تمام flows و fault cases
- [ ] Gate F — exact-head CI کامل و بدون weakening
- [ ] Gate G — پذیرش واقعی روی Windows target با Runner/Ollama/Codex
- [ ] Gate H — crash/restart/recovery و عدم از دست رفتن داده
- [ ] Gate I — مستندات، ADR، engineering log، runbook و rollback
- [ ] Gate J — تأیید کاربر از UI و سپس آماده‌سازی PR برای merge

هیچ gate با «کد نوشته شد»، «pytest سبز شد»، «صفحه باز شد»، «دکمه دیده شد» یا «API 200 داد» بسته نمی‌شود. برای هر gate evidence واقعی ثبت کن.

## 10. نحوهٔ گزارش‌دهی به مالک

در هر تحویل گزارش کوتاه و دقیق ارائه کن:
- commit/branch/PR و SHA
- defect و root cause
- فایل‌های تغییرکرده و علت مرزبندی
- تست‌های اجراشده با تعداد و نتیجه
- Browser E2E trace/artifact
- Windows target evidence
- گیت‌های باز و blocker دقیق
- ریسک باقیمانده

هرگز ادعای «تمام شد»، «رفع شد» یا «قابل استفاده است» بدون شواهد end-to-end نکن. اگر دسترسی به محیط هدف یا خطای واقعی موجود نیست، دقیقاً همان محدودیت را اعلام کن و تا حد ممکن بخش‌های مستقل را با test harness و fault injection تکمیل کن.

## 11. شروع فوری

اکنون:
1. وضعیت live main/PR/Actions/runهای فعال را دوباره بخوان؛ گزارش‌های snapshot را به‌روز کن.
2. هیچ اجرای فعال یا checkpoint را لغو/overwrite نکن.
3. اسناد و کدهای بخش 1 را بخوان.
4. inventory کامل UI مهندسی و جدول event → API → state transition را ایجاد کن.
5. گزارش root causeهای اثبات‌شده و موارد نیازمند reproduction را بنویس.
6. ابتدا Gate A و سپس Gate B را اجرا کن؛ مالکیت state/lock/recovery بر همهٔ کارهای UI مقدم است.
7. بعد از تثبیت معماری، Gate C تا J را به ترتیب اجرا کن.
8. وقتی ماژول مهندسی واقعاً قابل استفاده شد، همین فرایند را برای سایر ماژول‌ها و صفحات UI به ترتیب اولویت از `docs/PROJECT_STATUS.md` و `docs/UI_ACCEPTANCE.md` تکرار کن.

**اولین خروجی مورد انتظار:** نه refactor فوری و نه ادعای موفقیت؛ بلکه inventory قابل اجرا، جدول state transitions، reproهای مرورگر، فهرست root causeهای اثبات‌شده، و برنامهٔ تغییر کوچک با تست failing برای هر defect.
