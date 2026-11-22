# ممیزی مالکیت و fencing نوشتن‌های دامنه‌ای — 2026-11-22

## مبنا و دامنه

- مخزن: `cognitive-kernel/Personal-AI-assistant`
- مبنای دقیق: `main` در SHA `0367a3ebfd92ac196bb096f1a45cbfed5a61985d`
- PR یکپارچه‌سازی: [#837](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/837)، Draft
- این تغییر ادامهٔ baseline ادغام‌شدهٔ [#799](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/799) است؛ merge commit آن `852423ae8b862b834e007eada0033d087a37cd83` است.
- هدف، بازنویسی صف یا lease نیست؛ هدف اتصال قرارداد موجود owner + lease epoch + expiry به mutationهای دامنه‌ای و child-task scheduling است.

## قرارداد مشترک

1. worker فقط با `TaskContext` حاصل از claim معتبر می‌تواند mutation منتسب به همان task را انجام دهد.
2. پیش از mutation، تراکنش SQLite با `BEGIN IMMEDIATE` باز می‌شود.
3. داخل همان تراکنش، `TaskContext.assert_current` با task type و payload مورد انتظار اجرا می‌شود.
4. فقط پس از تأیید task ID، owner token، epoch و expiry، mutation دامنه‌ای یا درج child task انجام می‌شود.
5. worker قدیمی یا reclaimed با `TaskManagerError` متوقف می‌شود؛ نتیجهٔ مدل/منبع قدیمی نباید در job، evidence، candidate، graph، experience یا child queue ثبت شود.
6. مسیرهای مستقیم و غیر worker که عمداً `task_context=None` دارند حفظ شده‌اند؛ این مسیرها مجوز bypass برای workerهای asynchronous نیستند.

## جدول transition و محل fencing

| دامنه / transition | owner و precondition | transaction و محل بررسی | postcondition / retry |
|---|---|---|---|
| Claim و reclaim صف | TaskWorker با owner token جدید و epoch افزایشی | baseline #799 در task lease manager؛ claim در تراکنش اتمیک | epoch قبلی باطل می‌شود؛ retry همان checkpoint معتبر را می‌خواند |
| ایجاد/اجرای learning job | task type و payload دقیق `learning.run_job` و `job_id` | `app/learning.py` و `app/learning_tasks.py`؛ پیش از claim دامنه‌ای، evidence، budget، result و failure settlement | worker stale هیچ job/evidence/budget/escalation جدیدی ثبت نمی‌کند |
| curriculum و researched path | task type `CURRICULUM_TASK_TYPE` و payload موضوع | `app/learning_curriculum_tasks.py`؛ source-body و researched-path persistence و سپس child scheduling | source/path فقط با lease جاری ثبت می‌شود؛ child lesson فقط پس از تأیید parent enqueue می‌شود |
| درج child task | parent TaskContext و task type/payload والد | `app/task_queue_store.py:create_task`؛ assert در همان تراکنش پیش از INSERT | parent منقضی‌شده child نمی‌سازد؛ dedupe key موجود همچنان جلوی تکرار را می‌گیرد |
| factual verification | task type و payload factual verification | `app/factual_verification_tasks.py` و handler sandbox typed | نتیجهٔ verify به task جاری متصل می‌ماند؛ شکست/lease loss نباید نتیجهٔ قبلی را overwrite کند |
| knowledge lifecycle و graph review | task type و payload candidate/edge | `app/knowledge_lifecycle.py`, `app/knowledge_lifecycle_tasks.py`, `app/learning_knowledge_graph*.py` | تغییر lifecycle/edge فقط توسط owner معتبر انجام می‌شود |
| cloud discovery | task type و payload discovery | `app/cloud_resources.py`؛ candidate persistence، run update و child scheduling | candidateهای commit‌شده حفظ می‌شوند؛ pause/cancel در پنجرهٔ finalization settle و run abandoned reconcile می‌شود |
| experience و research | task type و payload experience/research | `app/chat_history.py`, `app/experience.py`, `app/experience_tasks.py`, `app/research_tasks.py` | stale task نمی‌تواند experience/history/research state را تغییر دهد |
| sandbox practical | task type و payload عملی ارزیابی | `app/learning_practical.py`, `app/sandbox_tasks.py` و ثبت handler در `app/main.py` | handler نوع task مورد انتظار را صریح می‌پذیرد؛ lease نامعتبر قبل از persistence رد می‌شود |
| Complete/fail/pause/cancel | همان owner و epoch claim | task manager و TaskContext موجود در main | رفتار صف و checkpoint baseline حفظ می‌شود؛ این PR قرارداد queue-level را بازنویسی نمی‌کند |

## ادغام تغییرات هم‌پوشان

- #819 و #825 هر دو `learning.py`، `learning_tasks.py` و `learning_durable_budget.py` را تغییر می‌دادند. نسخهٔ parent-task contract از #825 انتخاب شد؛ سه regression stale-epoch/lease-loss از #819 به تست‌های learning اضافه شد.
- `learning_curriculum_tasks.py` از #819 حفظ شد تا fencing ذخیرهٔ source body و researched path حذف نشود؛ دو نقطهٔ child scheduling از #825 نیز parent context و payload می‌گیرند.
- patch مربوط به `task_queue_store.create_task` در #823 و #825 یکسان بود و فقط یک بار وارد شد.
- `experience_tasks.py` دو تغییر مستقل داشت: fence کردن update تجربه از #824 و ارسال task type مشخص به sandbox handler از #826؛ هر دو حفظ شدند.
- `app/main.py` از current main گرفته شد و فقط دو فراخوانی sandbox با task type صریح اصلاح شدند؛ ثبت handlerهای learning/knowledge/factual که در #817/#827 و main وجود داشتند حفظ شده‌اند.

## Regressionهای هدفمند اضافه/حفظ‌شده

- stale epoch نمی‌تواند learning job یا escalation را تغییر دهد.
- reclaim حین generation مانع failure settlement توسط worker قدیمی می‌شود.
- stale curriculum lease نمی‌تواند source body یا researched path بنویسد.
- stale parent نمی‌تواند child researched lesson ایجاد کند.
- stale learning lease نمی‌تواند domain job را claim کند یا source-discovery fallback را persist کند.
- cloud discovery pause در فاصلهٔ commit candidate تا final run update settle می‌شود و daily chain حفظ می‌شود.
- تست‌های factual verification، knowledge lifecycle/graph، experience/research و sandbox practical از PRهای منبع حفظ شده‌اند.

## وضعیت شواهد

- branch از current main ساخته شده و نسبت به آن 42 فایل تغییر دارد؛ branch عقب‌ماندگی نسبت به main ندارد.
- PR #837 Draft است؛ exact-head Linux/Windows test suite، Windows storage، Browser E2E، CodeQL و dependency audit هنوز باید روی head نهایی آن اجرا و ثبت شوند.
- هیچ‌یک از این تغییرات در main ادغام نشده‌اند.
- این ممیزی به‌تنهایی اثبات target-host نیست: Windows Runner واقعی، ACL چندحسابی، Ollama/Codex واقعی، crash/restart و تأیید مالک همچنان gateهای جدا هستند.

## محدودیت و عدم تخریب

- هیچ migration، task، checkpoint، learning record، candidate، experience history یا PR موجودی حذف نشده است.
- هیچ branch منبع #819 یا #821–#826 حذف نشده است؛ تا عبور PR #837 از CI و تطبیق reviewها، آن‌ها به‌عنوان منبع قابل بازیابی نگه داشته می‌شوند.
- این سند ادعا نمی‌کند تمام mutationهای کل مخزن audit شده‌اند؛ دامنهٔ آن mutationهای تغییرکرده در PRهای Gate B فوق است. ممیزی کامل call-siteهای خارج از این دامنه و target-host هنوز باز است.


## الحاقیهٔ شواهد CI — 2026-11-22

PR #837 در exact head با SHA 908e9e3a79b03146a3b560c1d65f7361c1efffab تمام کنترل‌های مخزن را گذراند:

- Linux: 2511 passed, 14 skipped, 69 deselected.
- Windows: 2509 passed, 16 skipped, 69 deselected.
- Windows storage regressions: موفق.
- Real Docker sandbox integration: موفق.
- Browser/dashboard smoke: موفق.
- CodeQL، Analyze Python/JavaScript، pip-audit و bootstrapهای Ubuntu/macOS/Windows: موفق.
- یک شکست در اجرای اولیه فقط به mock قدیمی تست experience مربوط بود؛ mock برای پارامتر task type اصلاح شد و exact-head rerun سبز شد.

69 تست browser E2E از full-suite به‌صورت جداگانه اجرا می‌شوند؛ 5 تست sandbox نیز در workflow اختصاصی Docker اجرا شده‌اند. CI سبز فقط شواهد PR head است؛ تغییرات هنوز در main ادغام نشده‌اند و target-host Windows/Runner، crash/restart و تأیید مالک همچنان gateهای باز هستند.
