# Inventory و تحلیل ریشه‌ای UI ماژول Autonomous Engineering

- تاریخ snapshot: 2026-11-22
- مبنای بررسی زنده: `main=0367a3ebfd92ac196bb096f1a45cbfed5a61985d`
- PR اصلاح UI: [#834](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/834)، head در زمان ثبت اولیه `2bfdb15707ee1ed9cb8840acfa23f2cbff05a864`; پس از افزودن cleanup-lock regressionها head تغییر کرده و SHA نهایی باید از GitHub دوباره خوانده شود؛ وضعیت: باز، CI exact-head در انتظار.
- PR #817 (قرارداد خطای ساختاریافته) و PR جایگزین #827 (بازیابی گزارش Codex) در main ادغام شده‌اند. PR قدیمی #818 به‌علت مبنای متعارض بسته شد. PR #833 برای کنترل سایر ماژول‌ها عمداً Draft و تا تکمیل گیت‌های این ماژول متوقف نگه داشته شده است.
- این سند inventory و شواهد است؛ «کد نوشته شد» یا «API پاسخ داد» به‌تنهایی به معنی پذیرش عملیاتی نیست.

## 1. مرزهای مالکیت

| لایه | فایل/سرویس | مسئولیت و state |
|---|---|---|
| UI shell | `app/static/autonomous-engineering.html` | ساختار صفحه، فرم‌ها، دکمه‌ها، status region و بارگذاری اسکریپت‌های مستقل |
| UI transport | `app/static/autonomous-engineering-core.js` | timeout، JSON parsing، error code، correlation ID، retryability و safe details |
| UI controls | `app/static/autonomous-engineering-controls.js` | تنظیمات، مدل، ارسال task، dispatch، pause، resume و cancel |
| UI status | `app/static/autonomous-engineering-status.js` | readiness، pending task/checkpoint، active run، فعال/غیرفعال‌کردن dispatch |
| UI runs | `app/static/autonomous-engineering-runs.js` | تاریخچه، logs/diagnostics، حذف run پایان‌یافته و cleanup |
| UI progress | `app/static/autonomous-engineering-progress.js` | نمودار، scorecard، رخدادها و فیلتر تاریخ |
| UI bootstrap | `app/static/autonomous-engineering.js` | اتصال ماژول‌ها و نمایش هشدار load failure |
| API | `app/autonomous_engineering.py` | router زیر `/api/autonomous-engineering`؛ dispatch و عملیات GitHub/Ollama |
| API error contract | `AutonomousEngineeringAPIRoute` در main | تبدیل HTTP/validation/unexpected error به envelope امن و headerهای correlation |
| چرخهٔ اجرا | `scripts/autonomous_engineering_cycle.py` | مالک اجرای task، restore checkpoint، watcher، validation و PR handoff |
| state files | `app/autonomous_engineering_state.py` و state broker | lock انحصاری، نوشتن atomic، pending marker، owner metadata و cleanup |
| progress/Codex | `scripts/autonomous_engineering_progress.py` | اجرای Codex، خواندن JSONL، گزارش نهایی و snapshotهای progress |
| Runner | GitHub Actions + Windows Runner | اجرای واقعی task؛ مالک process tree و دسترسی‌های میزبان |

UI مستقیماً فایل‌های durable، checkpoint یا Runner را تغییر نمی‌دهد. این stateها باید از API و مالک اجرایی خود عبور کنند.

## 2. جدول event → API → state transition

| رویداد UI | API | پیش‌شرط/مالک | اثر state و retry |
|---|---|---|---|
| بازخوانی وضعیت | `GET /status` | فقط read؛ readiness و GitHub state باید قابل تأیید باشند | بدون mutation؛ status و pending-work snapshot |
| ذخیره تنظیمات | `POST /settings` | مدل نصب‌شده، Context، tools، Ollama و GitHub CLI معتبر | متغیرهای repository برای model/interval/mode تغییر می‌کنند؛ خطا باید بدون ذخیرهٔ ناقص گزارش شود |
| دانلود مدل | `POST /models/pull` سپس `GET /models/pull/{job_id}` | تأیید صریح کاربر و سرویس محلی Ollama | job دانلود به حالت queued/running/completed/failed می‌رود؛ مدل موجود حذف یا جایگزین نمی‌شود |
| ارسال task دستی | `POST /dispatch` با `task` | readiness کامل، نبود run فعال، نبود task/PR/cleanup مسدودکننده و checkpoint معتبر؛ در mode=scheduled نیز task صریح مجاز است | workflow به GitHub ارسال می‌شود؛ ورودی `resume_once` تنظیم نمی‌شود؛ نتیجهٔ timeout مبهم است و نباید کورکورانه retry شود |
| اجرای چرخه از صف | `POST /dispatch` بدون body | همان readiness؛ اگر checkpoint وجود دارد فقط همان task ادامه می‌یابد | dispatch/reconcile؛ task جدید ساخته نمی‌شود مگر admission آن مجاز باشد |
| توقف ایمن | `POST /pause` | GitHub CLI authenticated | `AUTONOMOUS_ENGINEERING_PAUSED=true`؛ اجرای جاری تا مرز امن/پایان workflow ادامه دارد و چرخهٔ جدید شروع نمی‌شود؛ این توقف، suspend لحظه‌ای process نیست |
| ادامه | `POST /resume` | GitHub CLI authenticated؛ Resume صریح مالک | pause variable برابر false و workflow dispatch با ورودی `resume_once=true` می‌شود؛ در mode=scheduled فقط همین اجرای خالی یک‌باره مجاز است؛ نتیجهٔ dispatch باید جداگانه از Runs تأیید شود |
| لغو اضطراری | `POST /cancel` | اجرای active از GitHub پیدا شود | GitHub run فعال cancel می‌شود؛ checkpoint/task durable پاک نمی‌شود |
| بازخوانی سوابق | `GET /runs` | GitHub API قابل دسترسی | read-only؛ stale response باید کنار گذاشته شود |
| مشاهده گزارش | `GET /runs/{run_id}/logs` | run ID باید متعلق به repository/workflow مورد انتظار باشد | job/step failure، diagnostic ذخیره‌شده و log sanitize‌شده نمایش داده می‌شود |
| حذف اجرای قدیمی | `DELETE /runs/{run_id}` | فقط run با status=completed | فقط history entry GitHub حذف می‌شود؛ run فعال قابل حذف نیست |
| پاک‌سازی دادهٔ موقت | `POST /cleanup` | preflight GitHub؛ روی POSIX قفل repository → durable-state → checkpoint و recheck؛ روی Windows broker خودش همین ترتیب قفل را می‌گیرد تا pipe client مستقیم نتواند آن را دور بزند | history، baseline، checkpoint و pending ownership marker حفظ می‌شوند؛ Windows فقط stateهای تحت مالکیت broker را پاک می‌کند و فایل‌های runtime در worktree را حفظ می‌کند تا پس از آزادشدن lock حذف نشوند |
| بازخوانی پیشرفت | `GET /progress?period=...` | read-only؛ custom date باید معتبر باشد | snapshot/history/events خوانده می‌شوند؛ فیلتر تاریخ جدید باید بر پاسخ قدیمی غلبه کند |

### رفتار polling

- status: هر ۳۰ ثانیه؛ refresh بعد از mutation باید منتظر poll قدیمی بماند و snapshot تازه بگیرد.
- run history: هر ۳۰ ثانیه؛ sequence number پاسخ قدیمی را کنار می‌گذارد.
- progress: هر ۵ ثانیه؛ اگر درخواست قبلی در جریان باشد، درخواست تازه queue می‌شود و period/date جدید از دست نمی‌رود.
- هر UI module باید مستقل fail شود؛ load failure یک module نباید سایر بخش‌ها یا هشدار خطا را از بین ببرد.

## 3. state transitionهای پایدار

| State | مجاز به چه کاری است؟ | transition بعدی |
|---|---|---|
| Ready + no pending work | dispatch task جدید یا چرخهٔ صف | queued/in_progress پس از تأیید GitHub |
| Active workflow | مشاهده progress/log؛ cancel اضطراری | completed یا cancelled |
| Paused | dispatch جدید و recovery ممنوع؛ task جاری تا مرز امن ادامه می‌یابد | resume، که pause variable را پاک و workflow را با resume_once=true dispatch می‌کند |
| Pending task/owner/PR/cleanup | task جدید ممنوع؛ فقط reconcile/ادامهٔ همان کار | پس از تعیین‌تکلیف durable blocker به Ready |
| Verified checkpoint | فقط ادامهٔ همان task | restore همان worktree/diff و ادامه |
| Unverified checkpoint | هیچ dispatch جدیدی مجاز نیست | بررسی مالک/یکپارچگی؛ fail-closed |
| Completed run | logs و حذف run قدیمی با تأیید | history حذف می‌شود؛ task state مستقل است |
| Cleanup blocked | cleanup ممنوع | رفع blocker با مالک state broker |
| Scheduled mode | push/schedule، recovery و empty workflow_dispatch بدون resume_once ممنوع؛ task دستی مجاز | task دستی یا Resume یک‌باره با resume_once=true؛ چرخهٔ بعدی فقط پس از تغییر mode به continuous |

## 4. reproduction و regressionهای موجود

در `tests/test_browser_e2e.py` سناریوهای زیر وجود دارند؛ نام تست‌ها و exact-head CI باید در هر تغییر دوباره بررسی شوند:

- رندر کنسول و readiness واقعی status endpoint؛ dispatch تا تکمیل readiness غیرفعال است.
- ارسال متن دستی با محدودیت طول و ارسال دقیق payload؛ متن به‌عنوان داده تلقی می‌شود.
- جلوگیری از dispatch تکراری و قفل پنجرهٔ visibility بعد از درخواست مبهم.
- خطای cancel و حفظ feedback پس از status refresh.
- کنترل pause/resume مطابق state برگشتی از server.
- نمایش run logs، failure steps، حذف run پایان‌یافته و cleanup.
- کنارگذاشتن پاسخ قدیمی run-history در برابر پاسخ جدید.
- حفظ انتخاب period جدید در زمان pending بودن درخواست progress قبلی.
- توضیح durable pending task/checkpoint و جلوگیری از task جدید.
- PR #834 یک regression HTTP اضافه می‌کند که از مسیر واقعی `/api/autonomous-engineering/dispatch` خطای 409 می‌گیرد و body/headerهای ساختاریافته را بررسی می‌کند؛ این تست producer واقعی را ثابت می‌کند، نه فقط mock مرورگر را.

Reproduction روی Windows target، Ollama/Codex واقعی و Runner با ACL واقعی هنوز انجام‌شده محسوب نمی‌شود.

## 5. root causeهای اثبات‌شده

| مورد | علت | اصلاح/شاهد |
|---|---|---|
| خطاهای API بدون شناسهٔ قابل پیگیری | routeها فقط detail عمومی یا exception خام داشتند و UI correlation metadata قابل اتکا نداشت | PR #817 در main قرارداد امن `code/message/correlation_id/retryable/safe_details` و headerهای `X-Error-Code`, `X-Correlation-ID`, `X-Retryable` را اضافه کرد |
| گزارش Codex خالی/UTF-8 خراب با وجود turn موفق | مسیر بازیابی به گزارش موقت سالم وابسته بود و JSONL نهایی معتبر را به‌درستی بازیابی نمی‌کرد | PR #827 در main بازیابی محدود از final agent message و completion marker معتبر را اضافه کرد؛ turn ناموفق/ناقص همچنان fail-closed است |
| UI diagnostics از headerها استفاده نمی‌کرد | transport لایهٔ UI فقط detail را مصرف می‌کرد و کد/شناسه/قابلیت retry را از دست می‌داد | PR #834 parser را به body و headerها متصل می‌کند؛ regression واقعی API افزوده شده است |
| cleanup admission race | client اپلیکیشن به‌تنهایی قفل repository را می‌گرفت؛ کلاینت محلی دیگری می‌توانست مستقیماً pipe broker را صدا بزند و cleanup را بدون آن قفل اجرا کند | PR #834 قفل repository را داخل C# broker و پیش از durable-state/checkpoint اعمال می‌کند؛ تست Windows live با نگه‌داشتن قفل از Python Runner، تلاش مستقیم pipe cleanup را باید با blocker `repository_execution_active` رد کند. POSIX همچنان قفل repository را در route می‌گیرد و سپس state/checkpoint را قفل می‌کند |
| پاسخ status قدیمی می‌توانست بعد از mutation نمایش داده شود | poll قبل از mutation ممکن بود پس از پاسخ جدید UI را overwrite کند | status module در PR UI از single-flight و refresh-after-mutation استفاده می‌کند؛ E2Eهای stale response باید exact-head سبز شوند |
| شکست load یک فایل JS می‌توانست وضعیت صفحه را مبهم کند | bootstrap باید خرابی یک ماژول را مستقل و قابل مشاهده نگه دارد | PR #834 هشدار load failure را assertive و مستقل از polling می‌کند |
| stop/pause semantics ممکن است با suspend اشتباه گرفته شود | GitHub workflow از UI pause variable را می‌خواند؛ process جاری به‌صورت ناامن kill یا suspend نمی‌شود | UI باید pause را «توقف امن پس از مرز جاری» معرفی کند؛ cancel جداگانه برای لغو run فعال است |

## 6. Gate status در snapshot فعلی

| Gate | وضعیت | دلیل |
|---|---|---|
| A — inventory/reproduction | در حال تکمیل | این inventory و جدول transitions ثبت شده؛ مرور نهایی تمام branchها و اجرای reproduction روی target host لازم است |
| B — state ownership/fencing/recovery | جزئی؛ cleanup lock order و recovery بررسی می‌شوند، ممیزی call-siteها pending | main شامل lease epoch و owner+epoch fencing است؛ PR #834 قفل repository را در خود broker اعمال می‌کند و ترتیب repository → durable-state → checkpoint را حفظ می‌کند؛ چون workflow پیش از repository lock در current.json می‌نویسد، broker وضعیت فعال/نامعلوم current.json را رد می‌کند و فایل‌های progress مجاز را با FileShare فقط Read/Delete قفل می‌کند تا writer غیرهمکار هم نتواند وسط cleanup بنویسد؛ تست Windows هر دو race را پوشش می‌دهد؛ POSIX runtime cleanup نیز زیر checkpoint lock است؛ Windows runtime files بعد از آزادشدن broker lock حذف نمی‌شوند؛ ممیزی کامل mutationها و crash recovery هنوز لازم است |
| C — structured errors/Codex diagnostics | baseline merged؛ regression UI در CI | PR #817 و #827 merged؛ PR #834 integration test و exact-head CI pending |
| D — UI state machine/fault isolation | پیاده‌سازی در PR #834؛ پذیرش pending | status/run/progress stale-request و module-load fault cases باید روی exact head سبز شوند |
| E — Playwright E2E | pending exact-head | تست‌های موجود و افزوده‌شده باید روی PR #834 اجرا شوند؛ نتیجه هنوز ادعا نمی‌شود |
| F — exact-head CI | pending | چهار workflow PR #834 باید روی SHA نهایی completed/success باشند |
| G — Windows target host | pending owner execution | نیازمند Windows Runner، Ollama، Codex و ACL واقعی |
| H — crash/restart/recovery | pending target host | باید قطع process/host و بازیابی همان task/checkpoint اثبات شود |
| I — docs/ADR/runbook/rollback | در حال تکمیل | این inventory ثبت شد؛ runbook و rollback/restore evidence هنوز لازم است |
| J — user acceptance | pending | مالک باید UI واقعی را ببیند و تأیید کند؛ تا آن زمان merge ممنوع است |

## 7. محدودیت‌ها و next steps

1. PR #834 را روی exact head کامل CI و review کن؛ هیچ تستی را حذف/شل نکن.
2. Gate B را از روی mutation call-siteهای state broker، checkpoint watcher، cycle controller، cleanup و process supervisor کامل کن؛ هر transition باید precondition، owner، lock/transaction، postcondition، retry و audit event داشته باشد.
3. روی Windows target با سرویس‌های واقعی Playwright، Runner، Ollama و Codex را اجرا کن و run/job/artifact IDs را ثبت کن؛ secretها باید redact شوند.
4. crash/restart و restore/rollback را با task/checkpoint موجود آزمایش کن؛ هیچ task یا checkpoint را برای پاک‌سازی آزمایشی حذف نکن.
5. پس از تکمیل Gate A–I، مالک UI را تأیید می‌کند؛ فقط سپس PR آمادهٔ merge می‌شود.
6. تا پایان ماژول Autonomous Engineering، PR #833 و توسعهٔ سایر ماژول‌ها در حالت Draft می‌مانند.

### اصلاحات Gate B که در PR #834 اضافه شده‌اند

- Windows broker برای `POST /v1/cleanup` خودش `repository-execution.lock` را پیش از `autonomous-durable-state.lock` و `autonomous-checkpoint-store.lock` می‌گیرد؛ بنابراین کلاینت مستقیم pipe نمی‌تواند قفل UI را دور بزند.
- تست live Windows، قفل repository را با همان Python helper مورد استفادهٔ Runner می‌گیرد و درخواست مستقیم broker را در زمان قفل باید با `repository_execution_active` رد کند؛ همچنین یک Python progress writer فایل `current.json.tmp` را باز نگه می‌دارد و broker باید با `progress_writer_active` cleanup را بدون حذف فایل‌ها رد کند.
- روی Windows، broker قفل را قبل از آزادکردن اجرا و پاک‌سازی state می‌گیرد؛ فایل‌های runtime داخل worktree پس از بازگشت broker در اپلیکیشن حذف نمی‌شوند، چون حذف پس از آزادشدن قفل race ایجاد می‌کند. این فایل‌ها در Windows عمداً در فهرست preserved می‌مانند.
- روی POSIX، route قفل repository را می‌گیرد، سپس cleanup زیر durable-state و checkpoint-store lock انجام می‌شود و runtime files در همان بازهٔ repository lock حذف می‌شوند.
- خطای CI ناشی از `ModuleNotFoundError: scripts` با انتقال repository/checkpoint lock helperها به بستهٔ قابل‌نصب `app` و تبدیل فایل‌های scripts به compatibility wrapper رفع شده است؛ `pyproject.toml` و package discovery دست‌نخورده مانده‌اند. تست import و Windows service integration روی exact head باید تأییدش کند.

### قفل مسیر انتشار PR

- انتشار PR از CLI زیرفرمان `publish` فقط باید توسط subprocess فرزند همان cycle controller انجام شود که قفل repository را با همان `run_id` نگه داشته است.
- تابع `require_parent_repository_execution_lock` در بستهٔ `app` قفل OS را به‌صورت non-blocking بررسی و metadata مالک را با `os.getppid()`، `run_id` و hostname تطبیق می‌دهد؛ فراخوانی مستقیم CLI بدون parent lock یا با run ID متفاوت رد می‌شود.
- `scripts/autonomous_engineering_execution_lock.py` فقط compatibility wrapper است؛ implementation مشترک در بستهٔ `app` قرار دارد.
- تست subprocess، فراخوانی مستقیم و run ID نامطابق را رد و فرزند controller قفل‌دار را قبول می‌کند. این guard به‌جای تغییر فایل‌های محافظت‌شدهٔ cycle controller اضافه شده است.


## الحاقیهٔ راستی‌آزمایی زنده — 2026-11-22

### مبنای مخزن و تعیین تکلیف PRها
- `main` اکنون روی `0367a3ebfd92ac196bb096f1a45cbfed5a61985d` است؛ این commit، PR #827 را ادغام کرده و مبنای قبلی `91b7e062e21d06d774c2f1f394bc72e4eb69af2c` را جلو برده است.
- پس از بررسی head/base، فایل‌های تغییرکرده و ancestry، سه PR تکراری بسته شدند؛ branchها حذف نشدند:
  - #829 به‌عنوان تکرار اصلاحات UI که در #834 روی main فعلی بازپایه‌گذاری شده‌اند بسته شد؛ تولیدکنندهٔ خطای ساختاریافتهٔ backend از PR #817 از قبل در main است.
  - #831 بسته شد؛ head آن سه commit پشت head نهایی #823 بود و هیچ تغییر مستقلی نسبت به #823 نداشت.
  - #832 بسته شد؛ head و base SHA آن دقیقاً با #828 یکسان بود.
- اکنون ۱۲ PR باز باقی است. همهٔ PRهای بدون CI کامل یا دارای خطای بررسی به Draft تبدیل شده‌اند تا merge ناخواسته رخ ندهد:
  - #834: اصلاح UI و قفل cleanup؛ Draft، head `4205d471bfba5318c267959f09ce44f8c0695a20`، base=main. CodeQL، Analyze، pip-audit و dashboard-smoke موفق‌اند؛ test، Full Windows و bootstrapها هنوز queued هستند. این وضعیت CI کامل نیست.
  - #820: نسخهٔ قدیمی‌تر اصلاح UI و مستندات ممیزی/roadmap؛ Draft. بخش کد UI با #834 هم‌پوشانی دارد، اما اسناد تاریخی منحصربه‌فرد آن هنوز به #834 منتقل/تطبیق داده نشده‌اند؛ تا حفظ آن اطلاعات بسته نشود.
  - #828: اصلاح polling با backoff، jitter، visibility و teardown؛ Draft و وابسته به branch غیر-main `test/autonomous-engineering-ui-resilience-20261121`. Exact-head CI شکست دارد: چهار تست، از جمله assertionهای متنی شکننده و انتظار تعداد poll بیشتر از رخداد واقعی؛ dashboard-smoke نیز timeout دارد. تغییرات polling آن هنوز در #834 ادغام نشده‌اند.
  - #819: fencing گستردهٔ learning domain؛ Draft. دامنهٔ آن با #825 هم‌پوشانی دارد، ولی فایل‌های candidate/review channel در #819 منحصربه‌فردند. review comment حل‌نشده دربارهٔ persistence شواهد توسط worker stale دارد؛ ادعای پاسخ در thread به‌تنهایی جای exact-head CI را نمی‌گیرد.
  - #821: factual verification fencing؛ Draft، exact-head checks/status در دسترس نیست.
  - #822: knowledge lifecycle و graph fencing؛ Draft، exact-head checks/status در دسترس نیست.
  - #823: cloud discovery fencing؛ Draft. شامل ancestor کد #831 است؛ reviewهای قبلی pause/finalization و migration assertions باید با head فعلی بازبینی شوند؛ exact-head CI هنوز اثبات نشده.
  - #824: experience و research fencing؛ Draft، exact-head checks/status در دسترس نیست.
  - #825: learning runner و child-task fencing؛ Draft. با #819 در چند فایل مشترک است ولی scopeهای مستقل دارد؛ exact-head checks/status در دسترس نیست.
  - #826: sandbox practical assessment fencing؛ Draft، exact-head checks/status در دسترس نیست.
  - #833: lifecycle کنترل‌های TaskRuntime و workerهای مستقل؛ Draft و خارج از اولویت تا پایان ماژول مهندسی. آخرین CI شامل ۷ شکست تست است: assertionهای migration قدیمی (نسخه‌های ۷/۴۸ در برابر ۸/۴۹)، TypeError در module-control و نبود پوشهٔ backup در fixture؛ dashboard-smoke نیز در انتظار card count timeout شده است.
  - #835: lifecycle مستقل Autonomous Engineering؛ Draft و وابسته به #833. CI شکست دارد: assertionهای migration، تست قرارداد recovery که هنوز متغیر `EXECUTION_MODE` را انتظار دارد، و dashboard-smoke با timeout در انتظار تعداد cardها.
- PRهای #819 و #821 تا #826 همگی بر base قدیمی `91b7e062...` ساخته شده‌اند؛ قبل از هر merge باید با main فعلی مقایسه و exact-head CI مجدداً اجرا شود. هیچ‌کدام از آن‌ها به دلیل نبود check-run به‌عنوان سبز تلقی نمی‌شوند.
- هیچ PRی در این مرحله merge نشد؛ هیچ task، checkpoint، داده یا branchی حذف نشد.

### شواهد و اصلاح اولویت Gateها
- #834 روی head فعلی بخشی از CI را گذرانده، اما تمام workflowها هنوز کامل نشده‌اند؛ پذیرش Windows target، Runner/Ollama/Codex واقعی، crash/restart و تأیید مالک همچنان انجام‌نشده‌اند.
- شکست‌های #833 و #835 مربوط به مسیر کنترل ماژول‌های دیگرند و نباید با دستکاری/تضعیف تست‌ها یا تغییر migrationهای موجود برای سبزکردن سریع رفع شوند. ابتدا باید نسخهٔ migration هر store و fixtureهای تستی به‌صورت مستقل بررسی شوند.
- #828 شواهد ارزشمندی دربارهٔ polling lifecycle دارد؛ چون branch آن stacked است، باید فقط تغییرات واقعی polling و تست‌های رفتاری آن به branch مبتنی بر main منتقل شوند، نه کل diff وابسته به PR #820.
- Gate A: inventory و state-transition table اولیه موجود است؛ تکمیل selector/event/API mapping، reproductionهای واقعی و ثبت نتیجهٔ تمام controls هنوز لازم است.
- Gate B: queue-level lease fencing از PR #799 در main وجود دارد؛ domain write fencing در #819 و #821 تا #826 هنوز باز و اثبات‌نشده است. cleanup-lock اصلاح‌شدهٔ #834 نیز تا exact-head Windows integration و target-host acceptance پذیرفته‌شده محسوب نمی‌شود.
- Gate C تا F فقط بخشی از baseline را دارند؛ Gate G و H نیازمند میزبان Windows مالک هستند؛ Gate J نیازمند مشاهده و تأیید مستقیم UI توسط مالک است.


## الحاقیهٔ اعتبارسنجی Windows و اصلاح خطاهای CI — 2026-11-22

### نتیجهٔ exact-head قبلی #834
- Browser E2E (Playwright) موفق شد؛ CodeQL، Analyze، pip-audit و bootstrapهای Linux/macOS/Windows نیز موفق بودند.
- job لینوکس `test` شکست خورد: ۲ تست cleanup به دلیل patch نشدن وابستگی `execution_state_directory` در ماژول lock به مسیر سیستم `/usr/local/share/PersonalAIAssistant` رفتند؛ یک assertion متنی نیز facade قدیمی checkpoint lock را به‌اشتباه انتظار داشت.
- `windows-storage-regressions` شکست خورد: فرایند فرزند هنگام خواندن metadata از همان byteای که parent با `msvcrt.locking` قفل کرده بود `PermissionError` گرفت؛ assertion facade checkpoint lock نیز stale بود.
- Full test suite ویندوز در زمان ثبت این الحاقیه هنوز در حال اجرا بود؛ هیچ نتیجهٔ موفقی برای آن فرض نشده است.

### اصلاحات آماده‌شده در branch جایگزین
- metadata مالک repository lock اکنون از byte شمارهٔ ۱ خوانده/نوشته می‌شود و byte صفر فقط برای قفل cross-process باقی می‌ماند؛ این کار خواندن metadata توسط child را بدون تداخل با byte-range lock ویندوز ممکن می‌کند و با قفل byte صفر C# broker سازگار می‌ماند.
- تست‌های cleanup هر دو ماژول مصرف‌کنندهٔ state directory را به مسیر موقت یکسان متصل می‌کنند.
- تست معماری checkpoint lock اکنون implementation واقعی زیر `app/` و compatibility facade زیر `scripts/` را جداگانه بررسی می‌کند.
- این اصلاحات در branch `fix/autonomous-engineering-first-module-validation-20261122` از head قبلی #834 ساخته شده‌اند. هنوز exact-head CI ندارند و merge نشده‌اند.


---

## Live audit update — 2026-11-22 پس از ادغام PR #837

### مبنا و وضعیت PRها
- main پس از squash merge شماره #837 روی SHA 9d97d2e3fa8f72429e6bb5510192ba70a6d376d8 است. PR #837 از head دقیق 80c126734123a5e84596d320ff625278495764e0 ادغام شد.
- CI head #837: Linux 2511 passed / 14 skipped / 69 deselected؛ Windows 2509 passed / 16 skipped / 69 deselected؛ Windows storage، Browser/dashboard smoke، real Docker sandbox، CodeQL و dependency audit موفق.
- #819 و #821–#826 به‌عنوان source PRهای #837 بسته شدند؛ branchهایشان حذف نشده‌اند. #820 و #834 به‌عنوان نسخه‌های قبلی UI در #836 حفظ و بسته شدند. #833 و #835 به علت شکست تست‌های lifecycle بسته شدند. #828، #829، #831 و #832 نیز بسته‌اند.
- تنها PR باز در snapshot فعلی #836 است؛ Draft. head قبلی آن fc8b039dce7c8ea642ee850a5e0de99ecd8fefc5 تمام CI را گذراند: Linux 2491/14/77، Windows 2489/16/77، Windows storage، Browser E2E/dashboard-smoke، CodeQL، Analyze و pip-audit. head ترکیبی جدید باید دوباره CI شود.
- هیچ branch منبع، task، checkpoint، دادهٔ durable، ACL یا state اجرای فعال حذف نشده است.

### Inventory کنترل‌ها و APIها
| selector/control | event | API | state owner / رفتار |
|---|---|---|---|
| #refresh-agent | click | GET /status | readiness، pending work و checkpoint؛ read-only |
| #agent-settings-form | submit | POST /settings | model/mode/interval |
| #pull-model | click + consent | POST /models/pull؛ GET /models/pull/{job_id} | مدل‌دانلود؛ قفل دکمه تا terminal state |
| #agent-task-form / #send-agent-task | submit | POST /dispatch با task | task دستی؛ digest و owner durable |
| #dispatch-agent | click | POST /dispatch بدون body | dispatch/resume؛ timeout مبهم retry کور ندارد |
| #pause-agent | click | POST /pause | توقف admission/چرخهٔ بعدی، نه kill قطعی اجرای جاری |
| #resume-agent | click | POST /resume | resume و dispatch کنترل‌شده |
| #cancel-agent | click + confirm | POST /cancel | لغو run فعال؛ task/checkpoint حفظ می‌شود |
| #refresh-runs | click | GET /runs | sequence guard برای رد پاسخ قدیمی |
| delegated actions در #recent-runs | click | GET /runs/{id}/logs؛ DELETE /runs/{id} | logs و حذف فقط run پایان‌یافته |
| #cleanup-agent-temp | click + confirm | POST /cleanup | preflight و lock order؛ دادهٔ durable حفظ می‌شود |
| #progress-period، #progress-start، #progress-end | change | GET /progress | coalesced refresh و رد پاسخ stale |
| #refresh-progress | click | GET /progress | chart/events و scorecard |

۲۲ کنترل/ورودی تعاملی اصلی به‌علاوهٔ delegated actions تاریخچه وجود دارد. status، checkpoint، metrics و پیام‌ها read-only هستند. ترتیب JS: core → runs → status → controls → progress → orchestrator؛ هر feature قرارداد version-1 مستقل دارد.

### جدول state transition
| transition | precondition | owner/lock | postcondition |
|---|---|---|---|
| Ready → dispatch | status/GitHub معتبر؛ no active run یا pending task/owner/PR/checkpoint؛ readiness معتبر | API admission، workflow concurrency، durable-state lock | task/owner با digest ثبت می‌شود؛ dispatch مبهم دوباره ارسال نمی‌شود |
| Queued → Running | workflow gate و concurrency slot | self-hosted Windows Runner؛ repository lock و task lease epoch | همان task اجرا می‌شود؛ worker stale نمی‌تواند write کند |
| Running → checkpointed | controller و watcher متعلق به همان run/task | repository lock و checkpoint-store lock؛ atomic metadata/index | checkpoint قابل verify/resume باقی می‌ماند |
| Running → retry | شکست طبقه‌بندی‌شده و بودجهٔ retry | controller/process-tree owner | همان task/branch/checkpoint ادامه می‌یابد؛ task جدید انتخاب نمی‌شود |
| Running → PR pending | completion marker، local tests و exact-head CI سبز | child publisher با parent lock و durable-state lock | pending PR ثبت می‌شود؛ review مالک لازم است |
| PR pending → accepted | merge exact reviewed head و post-merge CI | owner/recovery با تطبیق head/base/merge SHA | فقط بعد از این نقطه task/checkpoint قابل settle است |
| Pause → Resume | pause persisted و reconciliation | GitHub repository variable و workflow gate | dispatch جدید پس از resume |
| Active → Cancelled | run فعال دقیق شناسایی شود | GitHub Actions cancellation | task/checkpoint برای recovery باقی می‌مانند |
| Cleanup → blocked/completed | no active owner، no pending marker، checkpoint inactive/verified | repository → durable-state → checkpoint؛ Windows broker نیز repository lock می‌گیرد | فقط telemetry موقت پاک می‌شود |

### Reproduction، root cause و gaps
- Browser suite روی head قبلی #836 شامل ۷۲ تست مستقل بود: render/readiness، dispatch، model pull، two-tab duplicate prevention، cancel، pause/resume، logs/delete/cleanup، stale status/run/progress، checkpoint blockers، structured errors، module fault isolation، polling visibility/backoff/jitter، RTL/mobile/keyboard و WCAG.
- workflow قبلی trace/screenshot artifact نگه نمی‌داشت؛ head جدید trace ZIP، screenshot dashboard و diagnostics مربوط به console/page/request/HTTP failure را ذخیره و upload می‌کند.
- کد قبلی options.timeoutMs را می‌گرفت ولی به AbortController نمی‌داد؛ timeout polling مدل به default ۱۲۰ ثانیه وابسته می‌شد. fix و browser regression برای timeout سفارشی ۴۰ms در head جدید اضافه شده‌اند؛ CI آن pending است.
- cleanup Windows پیش‌تر repository execution lock را در خود Named Pipe broker enforce نمی‌کرد. #836 lock order repository → durable-state → checkpoint را در broker اجرا می‌کند. byte صفر برای lock رزرو و metadata از byte یک ذخیره می‌شود؛ publish child نیز parent PID/run/host را بررسی می‌کند.
- structured error parser، error code و correlation header را تا UI می‌رساند؛ correlation ID تا workflow dispatch/run/job/task هنوز end-to-end اثبات نشده است.
- Browser E2E deterministic و API-intercepted است؛ target-host UI واقعی نیست. Windows Runner/Ollama/Codex، ACL، crash/restart، restore/rollback و owner acceptance هنوز NOT RUN هستند.

### Gate status
| Gate | وضعیت |
|---|---|
| A inventory/reproduction | mapping ثبت شد؛ fault matrix و مرور دستی همهٔ کنترل‌ها باز |
| B state/lease/lock | #837 در main؛ #836 lock order و parent guard؛ target Windows/ACL و audit خارج از scope باز |
| C errors/diagnostics | UI structured error حاضر؛ correlation propagation تا run/job/task باز |
| D UI state machine | modules، stale guards، backoff/visibility، queue؛ CI head جدید pending |
| E Playwright | deterministic coverage؛ trace artifact اضافه شد، CI pending |
| F exact-head CI | head قبلی سبز؛ head جدید pending |
| G Windows target | pending |
| H crash/restart | pending |
| I docs/rollback | inventory/ADR/roadmap به‌روزرسانی؛ restore evidence باز |
| J owner acceptance | pending؛ PR #836 Draft می‌ماند |

در زمان ممیزی workflow فعال main شماره 44995223588 در حال اجرای یک task مستقل بود؛ لغو یا دستکاری نشد. دسترسی به فایل‌سیستم میزبان شخصی، pause variable، Broker محلی و نصب واقعی Ollama/Codex در این اتصال وجود ندارد؛ این موارد NOT RUN هستند.


## 2026-11-23 addendum — browser-originated correlation identity

- مبنای بررسی کد: main `31349a99b197fe702e11e8a5dd53d52ba924575c`؛ اصلاح در PR #846 انجام می‌شود.
- Core transport اکنون برای هر درخواست API یک شناسهٔ تصادفی امن ۳۲ کاراکتری hex تولید می‌کند (`crypto.randomUUID()` با fallback مبتنی بر `crypto.getRandomValues`) و آن را در `X-Correlation-ID` می‌فرستد.
- پاسخ موفق/ناموفق، timeout و خطای شبکه همان شناسه را در payload/error و متن feedback UI نگه می‌دارند؛ در نتیجه timeout مبهم dispatch نیز شناسهٔ قابل جست‌وجو دارد.
- API آن را به ورودی workflow منتقل می‌کند؛ workflow run/job، progress diagnostics و durable task owner metadata همان correlation ID را ثبت می‌کنند.
- Regressionهای مرورگر برای dispatch دستی و round-trip شناسه، timeout و خطای شبکه اضافه شده‌اند. exact-head CI و target-host پذیرش هنوز معیار نهایی هستند.


## 2026-11-23 addendum — validation evidence

- Exact implementation code SHA: `d993038033d5d15cca4a4a6c63c979c081c89aad`.
- Ubuntu full suite: 2,555 passed; Windows full suite: 2,550 passed; Windows storage regressions and Python bootstrap across Windows/Ubuntu/macOS passed.
- Browser E2E passed with all six UI assets returning HTTP 200 and zero unexpected browser errors; artifact #14797757907.
- CodeQL and dependency audit passed.
- These results close repository CI gates only. Local Windows UI/Runner/Ollama/Codex acceptance remains pending.

---


---

## الحاقیهٔ 2026-11-24 — ممیزی مجدد بر اساس وضعیت فعلی main

این الحاقیه وضعیت تاریخی inventory مورخ 2026-11-22 را بازنویسی نمی‌کند؛ موارد زیر snapshot جدید و مرجع جاری برای ادامهٔ کار هستند.

### یافته‌های جدید و اصلاحات

| حوزه | یافته | اصلاح/وضعیت |
|---|---|---|
| UI status refresh | دو mutation می‌توانستند پس از یک poll مشترک، درخواست‌های status هم‌زمان بفرستند و snapshot قدیمی‌تر را جایگزین کنند | PR #864 این refreshها را سریالی می‌کند؛ exact-head CI کامل موفق است |
| Broker provisioning | timeout پردازهٔ Python/PowerShell والد، تضمین نمی‌کند UAC-elevated child متوقف شود؛ قفل والد ممکن است آزاد شود درحالی‌که فرزند هنوز ACL/service را تغییر می‌دهد | finding در review PR #863 ثبت شد؛ PR #865 قفل machine-wide را داخل child اضافه کرده؛ exact-head CI و Windows integration هنوز pending هستند |
| Safe Pause | مرحلهٔ پیش از task مقدار vars.AUTONOMOUS_ENGINEERING_PAUSED را از workflow context می‌خواند؛ run منتظر concurrency ممکن است snapshot قدیمی داشته باشد | PR #866 مقدار جاری را از Actions Variables API پس از گرفتن concurrency slot می‌خواند؛ exact-head CI pending است |
| Emergency Cancel | انتخاب اولین run فعال از فهرست newest-first ممکن بود run تازه‌صف‌شده را لغو کند و run قدیمی‌ترِ درحال‌اجرا را باقی بگذارد | PR #867 وضعیت job واقعی codex را با gh run view بررسی می‌کند و اجرای دارای job درحال‌اجرا را انتخاب می‌کند؛ exact-head CI pending است |

### وضعیت Gateها

- **A — Inventory و reproduction:** inventory و state-transition mapping موجود است؛ بازتولیدهای میزبان واقعی و بررسی همهٔ کنترل‌ها هنوز تکمیل نشده‌اند.
- **B — Ownership/fencing/recovery:** lease epoch و owner fencing در main وجود دارند؛ ممیزی تمام domain mutation call-siteها، رفتار owner قدیمی، crash/restart و recovery واقعی هنوز لازم است. قفل elevated Broker در PR #865 هنوز پذیرفته نشده است.
- **C — Error contract/observability:** structured errors و correlation پایه موجودند؛ خطاهای timeout/ambiguous و correlation باید در مسیرهای جدید نیز exact-head و target-host تأیید شوند.
- **D — UI state machine:** اصلاح refresh race در PR #864 روی exact head با CI کامل موفق شده؛ Pause live gate و Cancel priority در PRهای #866/#867 هنوز CI pending دارند.
- **E — Browser E2E:** PR #864 روی head دقیق Browser E2E را گذرانده؛ PRهای جدید هنوز نیازمند اجرای مجدد روی head نهایی هستند.
- **F — Exact-head CI:** فقط PR #864 در این snapshot تمام workflowهای لازم را روی head دقیق گذرانده است. وضعیت PRهای #863 و #865–#867 باید جداگانه و روی آخرین SHA بررسی شود.
- **G — Windows target host:** pending؛ نیازمند حساب غیرادمین، UAC، LocalService Broker، Runner واقعی/ناموجود، ACL و Ollama/Codex واقعی.
- **H — Crash/restart/recovery:** pending؛ باید parent timeout با child Elevated زنده، crash در حین تغییر ACL، restart و حفظ checkpoint روی Windows واقعی آزمایش شوند.
- **I — Documentation/rollback:** این الحاقیه و docs/UI_ACCEPTANCE.md به‌روز شدند؛ runbook دقیق بازیابی abandoned mutex و شواهد restore/rollback هنوز لازم است.
- **J — Owner acceptance:** pending؛ همهٔ PRها Draft باقی می‌مانند و هیچ merge خودکاری انجام نمی‌شود.

### محدودیت و ایمنی

- وضعیت CI یک PR یا SHA به PR دیگر یا به main تعمیم داده نمی‌شود.
- Browser E2E و Windows hosted CI جایگزین پذیرش میزبان واقعی نیستند.
- هیچ task، owner metadata، checkpoint، database، ACL، Runner registration یا workflow پایدار حذف یا بازنویسی نشده است.
- پیش از merge باید تمام checkها روی exact head سبز باشند و گیت‌های target-host/owner acceptance تکمیل شوند.



### حاقیهٔ Safe Pause و Execution Mode — 2026-11-24

- کنترل workflow پس از گرفتن concurrency slot، مقدار زندهٔ Pause و Execution Mode را می‌خواند.
- کنترلر Python پس از گرفتن repository execution lock و پیش از durable-state recovery یا شروع task هر دو مقدار را دوباره می‌خواند و پیش از dispatch چرخهٔ بعدی پس از ادغام مالک نیز آن‌ها را بررسی می‌کند.
- Recovery نیز mode و Pause را پیش از طبقه‌بندی retry و بار دیگر پس از backoff، درست پیش از dispatch، بررسی می‌کند.
- نبود متغیر Pause (HTTP 404) به معنی false و نبود متغیر mode به معنی continuous است؛ خطای API یا مقدار نامعتبر fail-closed است.
- در حالت scheduled، runهای خودکار push/schedule و recovery که پشت concurrency منتظر مانده‌اند اجرا نمی‌شوند؛ task دستی مجاز است و Resume فقط با ورودی صریح resume_once یک اجرای خالی را مجاز می‌کند. Pause پس از شروع task، task جاری را قطع نمی‌کند؛ فقط شروع task/recovery بعدی را متوقف می‌کند. exact-head CI این تغییرات هنوز pending است.



## 2026-11-24 — live admission transition table (ADR-024)

| Trigger / event | Live precondition read | State transition / permitted action |
|---|---|---|
| Push / schedule queued behind Codex concurrency | Workflow reads live Pause and execution mode after the Codex slot; cycle controller repeats after repository lock | Pause=true blocks. mode=scheduled blocks. mode=continuous + Pause=false permits one task only when the current UTC schedule gate admitted it. |
| Explicit manual task | Live Pause and mode; manual task payload is non-empty | Pause=true blocks. mode=scheduled does not block an explicit manual task. No resume_once marker is sent. |
| Owner Resume | POST /resume clears Pause and dispatches workflow_dispatch with resume_once=true | In scheduled mode one empty-task run is permitted. The marker is not inherited by the next cycle. |
| Automatic continuous chaining after owner merge | Cycle controller re-reads live mode and Pause immediately before dispatch | Dispatch next cycle only when live mode=continuous and Pause=false. Otherwise the accepted task remains complete and no next cycle is dispatched. |
| Automatic recovery | Recovery coordinator reads live mode/Pause before retry classification and after backoff; main workflow gate reads them again after concurrency | mode=scheduled or Pause=true blocks recovery. Recovery dispatch never sets resume_once. |
| Unmarked empty workflow_dispatch | Live gate checks mode, recovery_attempt, manual_task, and resume_once | In scheduled mode it is rejected; continuous mode may proceed if Pause=false and other admission checks pass. |
| Emergency cancel | POST /cancel; inspect run status and actual Codex job with gh run view --json jobs | Select the run whose Codex job is in_progress; fail closed on ambiguous/unreadable job state or excessive simultaneous in-progress runs. No task/checkpoint data is deleted. |

The implementation and rationale are recorded in [ADR-024](architecture/ADR-024-live-pause-and-execution-mode-admission.md). Exact-head CI and target-host acceptance remain pending; this table records the intended and tested contract, not a claim of end-to-end completion.

## 2026-11-24 — inherited mainline regressions

- PR #864's serialized post-mutation status refresh is now part of main and parent #863; PR #867's job-aware Cancel handler is carried by both parent branches. The child follow-ups must retain these changes when rebased.
- The progress cleanup queue fix and dedicated read-only Variables credential remain specific to #870 and await exact-head CI and secret-backed Runner validation.
- Broker Registry64 and same-thread/wrong-thread acknowledgement behavior remain specific to #869 and await Windows storage regression execution and target-host validation.
- Resume و manual dispatch از lock مشترک استفاده می‌کنند؛ timestamp مستقل Resume و guard متقابل مانع dispatch تکراری می‌شود. در صورت وجود run فعال فقط Pause برداشته می‌شود و run دوم صف نمی‌شود؛ خطای قطعی Pause را rollback می‌کند.

## الحاقیهٔ 2026-11-24 — وضعیت فعلی state ownership و کنترل‌های UI

| مسیر/رویداد | مالک state | قرارداد و وضعیت |
|---|---|---|
| status poll و post-mutation refresh | autonomous-engineering-status.js | statusRefreshPromise برای single-flight و statusMutationRefreshPromise برای سریال‌سازی refreshهای پس از mutation؛ پاسخ قدیمی نباید snapshot جدیدتر را overwrite کند. |
| pause/resume/cancel | UI controls → API → GitHub Actions | Pause فقط admission آینده را متوقف می‌کند؛ Resume در حالت scheduled باید یک dispatch یک‌بارهٔ صریح بسازد؛ Cancel باید job واقعی Codex را تشخیص دهد و task/checkpoint را حذف نکند. |
| run history/log/delete | autonomous-engineering-runs.js → API | sequence guard پاسخ‌های قدیمی؛ حذف فقط run پایان‌یافته و متعلق به workflow؛ cleanup از مسیر lockهای repository → durable state → checkpoint. |
| progress/chart | autonomous-engineering-progress.js → API | progress owner مستقل؛ refreshهای هم‌زمان coalesce می‌شوند. follow-up #870 رویداد cleanup را با queueIfBusy: true به refresh بعدی تبدیل می‌کند تا درخواست درحال اجرا باعث drop شدن refresh نشود. |
| live Pause/Mode | PowerShell gate + cycle/recovery controllers | خواندن Variables فقط با credential اختصاصی read-only؛ GITHUB_TOKEN برای عملیات workflow باقی می‌ماند؛ cycle secret را پیش از اجرای Codex از environment حذف می‌کند. |
| Broker setup mutex | PowerShell setup-lock module + Windows registry marker | marker با Registry64 ثابت؛ در شکست marker-write، process fallback fail-closed؛ acknowledgement فقط از thread مالک می‌تواند ownership قبلی را آزاد و قفل تازه بگیرد. |

### Root causes و شواهد به‌روز
- Refresh race: دو mutation می‌توانستند پس از انتظار برای یک poll مشترک، دو درخواست status موازی بسازند؛ در main با promise queue حل و در PR #864 روی exact head تست شد.
- Cancel race: workflow status به‌تنهایی کافی نبود؛ job Codex می‌تواند پشت schedule gate در صف باشد. main اکنون gh run view --json jobs را بررسی و اجرای دارای Codex job فعال را انتخاب می‌کند؛ exact-head PR #867 موفق شد.
- Cleanup/progress: اگر event پاک‌سازی هنگام درخواست progress فعال می‌رسید، refresh event بدون queue حذف می‌شد؛ اصلاح queueIfBusy: true در follow-up #870 است و CI آن هنوز pending است.
- Live settings: GITHUB_TOKEN مجوز Variables:read ندارد؛ #870 credential read-only جداگانه را در PowerShell، cycle و recovery به کار می‌گیرد و fail-closed است.
- Windows setup: marker رجیستری باید cross-bitness باشد و fallback فرایندی نباید با recursive mutex ownership دور زده شود؛ #869 مسیر Registry64 و acknowledgement same-thread/wrong-thread را تست می‌کند.

### وضعیت Gateها
- Repository-level UI tests برای #864 و #867: موفق روی exact head.
- Exact-head CI برای شاخه‌های فعلی #863/#869 و #866/#870: pending تا اجرای کامل روی آخرین SHA.
- Windows target-host، Runner واقعی، Ollama/Codex واقعی و owner acceptance: NOT RUN؛ هیچ‌کدام با CI شبیه‌سازی‌شده جایگزین نمی‌شوند.
- Resume و manual dispatch از reservation دو مرحله‌ای مشترک در State Broker استفاده می‌کنند؛ timestamp و lease در state محافظت‌شده ذخیره می‌شود، بین workerها و پس از restart باقی می‌ماند، و lease رهاشده به‌صورت محافظه‌کارانه grace تازه ایجاد می‌کند. در صورت وجود run فعال فقط Pause برداشته می‌شود و run دوم صف نمی‌شود؛ در صورت dispatch جدید، Pause فقط پس از پذیرش GitHub برداشته می‌شود.
- Cancel با dispatch/resume lock سریال می‌شود و run_id را از snapshot موفق UI می‌گیرد؛ سرور همان run را مستقیماً از GitHub می‌خواند، workflow و وضعیت فعال آن را اعتبارسنجی می‌کند، job Codex همان run را می‌سنجد و فقط همان شناسه را لغو می‌کند.
- ورودی‌های workflow_dispatch با gh workflow run --raw-field ارسال می‌شوند؛ متن manual_task و correlation_id دیگر مشمول @path expansion یا تبدیل نوع --field نیستند.
- cleanup در Windows تصمیم active-progress و lock را به State Broker محافظت‌شده واگذار می‌کند؛ مسیرهای مطلق فایل از پاسخ API حذف می‌شوند.


### پوشش ۱۴ یافتهٔ بازبینی (2026-11-24)

- Backend: raw-field، مسیر قفل‌دار مشترک dispatch/resume، reservation پایدار Broker، cancel بر اساس run_id، diagnostics محدود به failure/current page، cleanup بدون افشای مسیر مطلق و redaction گسترده.
- Codex: failure feedback پالایش‌شده در retry، طبقه‌بندی enum برای نبود پیام نهایی/نشانگر ناقص/نشانگر غیرپایانی، timeout محدود ۶۰۰ ثانیه برای report-only repair، و پذیرش نشانگر فقط در آخرین خط غیرخالی.
- UI: پیام چسبنده، refresh callback بدون Event argument، حفظ keyed run/log panels، حفظ Cancel/Pause/Resume در poll failure، معنای درست retryable، وضعیت خطای cleanup ناقص، تاریخ معتبر، fallback بازه، عدم بلعیدن timeout بدنهٔ response و اصلاح مسیر راه‌اندازی.
- وضعیت: پیاده‌سازی و regression test اضافه شده‌اند؛ exact-head CI، build/integration سرویس Windows Broker، browser E2E و پذیرش روی Windows واقعی هنوز اجرا/تأیید نشده‌اند.
