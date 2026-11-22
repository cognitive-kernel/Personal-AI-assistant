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
| ارسال task دستی | `POST /dispatch` با `task` | readiness کامل، نبود run فعال، نبود task/PR/cleanup مسدودکننده و checkpoint معتبر | workflow به GitHub ارسال می‌شود؛ نتیجهٔ timeout مبهم است و نباید کورکورانه retry شود |
| اجرای چرخه از صف | `POST /dispatch` بدون body | همان readiness؛ اگر checkpoint وجود دارد فقط همان task ادامه می‌یابد | dispatch/reconcile؛ task جدید ساخته نمی‌شود مگر admission آن مجاز باشد |
| توقف ایمن | `POST /pause` | GitHub CLI authenticated | `AUTONOMOUS_ENGINEERING_PAUSED=true`؛ اجرای جاری تا مرز امن/پایان workflow ادامه دارد و چرخهٔ جدید شروع نمی‌شود؛ این توقف، suspend لحظه‌ای process نیست |
| ادامه | `POST /resume` | GitHub CLI authenticated | pause variable برابر false و workflow dispatch می‌شود؛ نتیجهٔ dispatch باید جداگانه از Runs تأیید شود |
| لغو اضطراری | `POST /cancel` | اجرای active از GitHub پیدا شود | GitHub run فعال cancel می‌شود؛ checkpoint/task durable پاک نمی‌شود |
| بازخوانی سوابق | `GET /runs` | GitHub API قابل دسترسی | read-only؛ stale response باید کنار گذاشته شود |
| مشاهده گزارش | `GET /runs/{run_id}/logs` | run ID باید متعلق به repository/workflow مورد انتظار باشد | job/step failure، diagnostic ذخیره‌شده و log sanitize‌شده نمایش داده می‌شود |
| حذف اجرای قدیمی | `DELETE /runs/{run_id}` | فقط run با status=completed | فقط history entry GitHub حذف می‌شود؛ run فعال قابل حذف نیست |
| پاک‌سازی دادهٔ موقت | `POST /cleanup` | نبود run فعال؛ ابتدا و دوباره پس از گرفتن repository execution lock بررسی می‌شود؛ سپس lockهای durable-state و checkpoint گرفته می‌شوند | فقط temporary progress data حذف می‌شود؛ history، baseline، checkpoint و pending ownership marker حفظ می‌شوند؛ اگر Runner مالک worktree باشد 409 برمی‌گردد |
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
| Paused | dispatch جدید ممنوع | resume، که pause variable را پاک و workflow را dispatch می‌کند |
| Pending task/owner/PR/cleanup | task جدید ممنوع؛ فقط reconcile/ادامهٔ همان کار | پس از تعیین‌تکلیف durable blocker به Ready |
| Verified checkpoint | فقط ادامهٔ همان task | restore همان worktree/diff و ادامه |
| Unverified checkpoint | هیچ dispatch جدیدی مجاز نیست | بررسی مالک/یکپارچگی؛ fail-closed |
| Completed run | logs و حذف run قدیمی با تأیید | history حذف می‌شود؛ task state مستقل است |
| Cleanup blocked | cleanup ممنوع | رفع blocker با مالک state broker |

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
| cleanup admission race | cleanup قبلاً فهرست active GitHub runs را قبل از گرفتن قفل می‌خواند؛ یک workflow تازه می‌توانست بین آن بررسی و حذف فایل‌های progress شروع شود | PR #834 cleanup را زیر همان repository execution lock اجرا می‌کند، ترتیب قفل را repository → durable-state → checkpoint نگه می‌دارد و فهرست runها را بعد از lock دوباره بررسی می‌کند؛ دو regression برای lock-busy و recheck اضافه شده است |
| پاسخ status قدیمی می‌توانست بعد از mutation نمایش داده شود | poll قبل از mutation ممکن بود پس از پاسخ جدید UI را overwrite کند | status module در PR UI از single-flight و refresh-after-mutation استفاده می‌کند؛ E2Eهای stale response باید exact-head سبز شوند |
| شکست load یک فایل JS می‌توانست وضعیت صفحه را مبهم کند | bootstrap باید خرابی یک ماژول را مستقل و قابل مشاهده نگه دارد | PR #834 هشدار load failure را assertive و مستقل از polling می‌کند |
| stop/pause semantics ممکن است با suspend اشتباه گرفته شود | GitHub workflow از UI pause variable را می‌خواند؛ process جاری به‌صورت ناامن kill یا suspend نمی‌شود | UI باید pause را «توقف امن پس از مرز جاری» معرفی کند؛ cancel جداگانه برای لغو run فعال است |

## 6. Gate status در snapshot فعلی

| Gate | وضعیت | دلیل |
|---|---|---|
| A — inventory/reproduction | در حال تکمیل | این inventory و جدول transitions ثبت شده؛ مرور نهایی تمام branchها و اجرای reproduction روی target host لازم است |
| B — state ownership/fencing/recovery | جزئی؛ یک race مهم اصلاح‌شده، audit کلی pending | main شامل lease epoch و owner+epoch fencing است؛ PR #834 cleanup را با repository execution lock serialize و race بین active-run check و cleanup را می‌بندد؛ هنوز تمام mutation call-siteهای Runner/Broker و crash recovery باید با state transition table و تست پوشش داده شوند |
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
