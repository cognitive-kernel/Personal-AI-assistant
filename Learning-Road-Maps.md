**مدل کامل یادگیری خودتصحیح‌گر (از صفر تا صد)**

این سند، مدل توافق‌شده بین ماست. هدف آن ساخت سیستم یادگیری است که تا حد ممکن خودکار باشد، دخالت انسانی را به حداقل برساند، از اجماع مدل‌های بزرگ استفاده کند، و از شکست‌ها یاد بگیرد.

## وضعیت اجرا و چک‌لیست پیشرفت — بازبینی 2026-10-21

**وضعیت انتشار:** پیاده‌سازی در [PR #370](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/370)، شاخهٔ `feature/automated-learning-acceptance` انجام شده است و PR هنوز merge نشده. تیک‌های این بخش نشان‌دهندهٔ وضعیت کدِ همان شاخه‌اند، نه اینکه نسخهٔ فعلی `main` روی میزبان کاربر نصب یا پذیرفته شده باشد.

### ۱. سطوح اعتماد و اجماع

- [x] مهاجرت‌های schema برای trust levelهای `L0/L1/L2/L3/L-1` و وضعیت‌های `unverified/model_consensus/tested/human_verified/failed/conflicting/superseded`.
- [x] ثبت اجماع با hash دقیق نامزد، نسخهٔ prompt، مدل‌ها، رأی/دلیل، hash پاسخ و نتیجه؛ حداقل دو مدل متمایز و منبع ثبت‌شده برای `L1` لازم است.
- [x] اختلاف صریح یا تغییر hash، نامزد را به `L-1/conflicting` می‌برد و تجربهٔ تعارض ایجاد می‌کند؛ اجماع به‌تنهایی حقیقت تلقی نمی‌شود.
- [x] Migration 23 نسخهٔ prompt را برای peer response ذخیره می‌کند. رکوردهای قدیمی حذف نمی‌شوند، `legacy_unversioned` می‌شوند و نمی‌توانند اجماع جاری یا ارتقای L2 را تأمین کنند.
- [x] ارتقای `L2` فقط با consensus دارای نسخه و hash منطبق، منبع قابل‌ردیابی، نگاشت کامل ادعاها به تست و نتایج آزمون مطابق oracle معتبر مجاز است؛ پاسخ passing بدون پوشش ادعاها ارتقا نمی‌گیرد.
- [x] ارتقای `L3/human_verified` فقط با اقدام صریح انسان، دلیل ثبت‌شده و evidence/audit ممکن است.
- [ ] **باقی‌مانده:** هر ادعای استخراج‌شده هنوز رکورد مستقل و نسخه‌دار خود را در جدول claims ندارد؛ inventory ادعاها برای گیت پوشش تست استخراج می‌شود، اما به‌عنوان موجودیت مستقل Knowledge Item نگهداری نمی‌شود.

### ۲. جریان یادگیری، آزمون و منشأ

- [x] خروجی تولیدشده به‌عنوان نامزد موقت `L0` ثبت می‌شود؛ متن ورودی/منبع، URL، hash محتوا و evidence حفظ می‌شوند.
- [x] کانال‌های تحقیق وب، peer-AI و sandbox جداگانه اجرا و نتیجه/شکست هر کانال ثبت می‌شود؛ شکست یک کانال نباید نامزد و evidence موجود را حذف کند.
- [x] سابقهٔ آزمون دارای نام suite، نسخه، hashها، oracle، runner، expected/actual و وضعیت اجرا نگهداری می‌شود؛ اجرای نمونه‌های منبع به‌تنهایی مجوز ارتقای کل درس نیست.
- [x] تجربه‌های ساختاریافته شامل `consensus_failure`، `test_failure`، `usage_failure`، `successful_alternative`، `improvement` و `general` است.
- [x] نتیجهٔ ناموفق در فرم «کاربرد واقعی» به تجربهٔ `usage_failure` تبدیل می‌شود، اعتماد را پایین می‌آورد و خودتصحیح را به‌صورت خودکار آغاز می‌کند؛ این بازخورد گزارش کاربر است، نه oracle آزمایشگاهی.
- [x] پس از شکست/تعارض در خط لولهٔ بررسی نیز خودتصحیح خودکار اجرا می‌شود. سقف سه تلاش برای کل زنجیرهٔ نامزد و جایگزین‌هاست و با رزرو تراکنشی، تلاش هم‌زمان نمی‌تواند از سقف عبور کند.
- [x] هر جایگزین از `L0` آغاز می‌شود و بررسی وب/peer/sandbox را دوباره می‌گذراند؛ رأی یا تست والد به آن منتقل نمی‌شود.
- [x] پس از پذیرش جایگزین معتبر، دانش پایدار قبلی `superseded` می‌شود، لینک دوطرفهٔ predecessor/replacement حفظ می‌شود و رکوردها حذف نمی‌شوند. نامزد موقت قبلی در `learning_status` منسوخ می‌شود و آزمون مجدد آن مسدود است.
- [ ] **باقی‌مانده:** پایش خودکارِ همهٔ پاسخ‌ها و کارهای واقعی پیاده‌سازی نشده؛ ثبت usage failure فعلاً از طریق فرم بازخورد صریح کاربر انجام می‌شود.
- [ ] **باقی‌مانده:** پس از تمام‌شدن سه تلاش، صف اختصاصی escalation و اولویت‌بندی خودکار برای بازبینی انسان هنوز وجود ندارد؛ نامزد و سابقه برای بازبینی در UI باقی می‌مانند.
- [ ] **باقی‌مانده:** فیلدهای رابطه‌ای مستقیم مانند `related_knowledge_id` و `related_consensus_id` برای همهٔ تجربه‌ها یکپارچه نشده‌اند؛ ارتباط‌ها در حال حاضر از candidate/attempt IDs، evidence و توضیحات قابل‌ردیابی‌اند.

### ۳. ارائه‌دهنده‌ها و آزمون مستقل

- [x] adapterهای Ollama و انتخاب مدل مرکزی/پروفایل‌های چندمدلی برای وظایف به کد و UI تنظیمات متصل شده‌اند.
- [x] peer-AI از endpointهای OpenAI-compatible با محدودیت امنیتی HTTPS (به‌جز loopback)، محدودیت تعداد و API key از متغیر محیطی پشتیبانی می‌کند؛ پاسخ خام و نسخهٔ prompt ثبت می‌شوند.
- [ ] **باقی‌مانده:** باید حداقل دو provider/model واقعاً مستقل روی میزبان هدف در `PEER_AI_PROVIDERS_JSON` پیکربندی و اجرا شوند. CI این اتصال زنده یا استقلال واقعی آموزش مدل‌ها را اثبات نمی‌کند؛ تنظیم peerها هنوز UI مستقل ندارد.
- [ ] **باقی‌مانده:** oracleهای معتبر و مجموعهٔ آزمون دامنه‌ای برای Python، شبکه، Docker و دیگر حوزه‌ها باید گسترش پیدا کنند. peer-suggested tests صرفاً پیشنهادهای اکتشافی‌اند و oracle محسوب نمی‌شوند.
- [ ] **باقی‌مانده:** نسخه‌بندی prompt اجماع/peer پیاده‌سازی شده است؛ نسخه‌گذاری یکپارچهٔ همهٔ promptهای تولید درس، ارزیابی و اصلاح هنوز کامل نیست.
- [ ] **باقی‌مانده:** دانش پایدار `knowledge_items` فعلاً منبع مستقل retrieval برای پاسخ عادی نیست؛ بازیابی معنایی موجود روی حافظهٔ تجربهٔ مجاز/تأییدشده انجام می‌شود. برای RAG دانش پایدار باید retrieval با فیلتر سخت `L2/L3` و حذف `failed/conflicting/superseded` طراحی و آزموده شود.

### ۴. شاخص‌های موفقیت

- [x] `GET /api/learning/metrics` و کنترل UI برای شمارش trust levelها، وضعیت نامزدها، نوع تجربه‌ها، وضعیت تلاش‌های خودتصحیح، رویدادهای ماهانه، جایگزین‌های موفق و سهم دانش فعال `L2/L3` اضافه شده است.
- [x] شاخص‌ها تعداد رویدادهای ثبت‌شده را گزارش می‌کنند؛ در نبود مخرج یا تاریخچهٔ کافی، نتیجه به‌صورت `null` است و صفر/احتمال ساختگی تولید نمی‌شود.
- [ ] **باقی‌مانده:** کاهش واقعی درصد خطا، رشد نرخ جایگزین موفق و کوتاه‌شدن زمان حل هنوز از دادهٔ طولی و ارزیابی‌شده اثبات نشده است؛ شمارش رویداد به‌تنهایی معادل نرخ خطا نیست.

### ۵. گیت‌های پذیرش پیش از انتشار

- [x] CI روی commit کد `603ac02f1505`: [آزمون یکپارچهٔ sandbox و تست‌های داخل Docker](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39562927376)، [Browser E2E](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39562927296)، [ممیزی وابستگی‌ها](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39562927375) و رگرسیون‌های ذخیره‌سازی Windows موفق شدند.
- [ ] آزمون واحد استاندارد روی runner اصلی هنوز در حال اجراست؛ تا تکمیل آن، CI کل شاخه را کاملاً سبز در نظر نگیرید: [وضعیت آزمون‌های commit 603ac02](https://github.com/cognitive-kernel/Personal-AI-assistant/actions/runs/39562927414).
- [ ] ارزیابی زندهٔ مدل‌های محلی فارسی/انگلیسی، review انسانی مجموعهٔ ارزیابی نسخه‌دار، بررسی مقاومت در برابر prompt injection و سنجش پشتیبانی claimها از منابع.
- [ ] اجرای مهاجرت/backup/restore، شروع پاک و smoke test روی میزبان Windows/دادهٔ واقعی هدف؛ موفقیت CI جایگزین این پذیرش نیست.
- [ ] بازبینی مستقل امنیت شبکه و threat model مربوط به [Issue #52](https://github.com/cognitive-kernel/Personal-AI-assistant/issues/52).
- [ ] بازبینی دستی دسترس‌پذیری/صفحه‌کلید/صفحه‌خوان و پذیرش عملیاتی UI روی میزبان هدف.

---

---

### ۱. اهداف اصلی سیستم

- دانش با سرعت بالا رشد کند.
- دخالت انسان به حداقل ممکن برسد.
- هر دانش دارای سطح اعتماد مشخص و قابل ردیابی باشد.
- سیستم بتواند اشتباهات اجماع مدل‌ها را تشخیص دهد، ثبت کند و راه‌حل جایگزین پیدا کند.
- تمام تصمیم‌ها و تغییرات قابل ردیابی (provenance) باشند.

---

### ۲. سطوح اعتماد دانش (Trust Levels)

| سطح | نام | شرایط ورود | وضعیت | دخالت انسان |
|-----|-----|-----------|--------|-------------|
| **L0** | Raw | فقط استخراج شده (از سایت یا کاربر) | `unverified` | هیچ |
| **L1** | Model Consensus | حداقل ۲ مدل بزرگ مستقل آن را تأیید کرده‌اند + منبع اصلی موجود است | `model_consensus` | هیچ |
| **L2** | Tested | علاوه بر L1، با تست واقعی (اجرای کد، بررسی مستندات رسمی، یا تست عملی) تأیید شده | `tested` | هیچ |
| **L3** | Human Verified | انسان صراحتاً آن را تأیید کرده | `human_verified` | بله |
| **L-1** | Failed / Conflicting | تست شکست خورده یا اجماع مدل‌ها با واقعیت در تضاد بوده | `failed` یا `conflicting` | ترجیحاً هیچ (سیستم خودش جایگزین پیدا کند) |

**قوانین مهم:**
- هیچ دانشی نباید مستقیماً از L0 به L2 یا L3 برود.
- دانش‌های `failed` نباید در پاسخ‌های عادی استفاده شوند مگر اینکه راه‌حل جایگزین داشته باشند.
- سیستم باید همیشه سطح اعتماد فعلی را نمایش دهد.

---

### ۳. موجودیت‌های اصلی داده

#### ۳.۱. Knowledge Item (دانش)
- `id`
- `claim` (متن ادعای دانش)
- `domain` (مثلاً python, docker, networking و ...)
- `source_url` یا `source_type` (website / user / other)
- `raw_content` (متن اصلی منبع، در صورت وجود)
- `trust_level` (L0 تا L3 یا L-1)
- `status` (`unverified`, `model_consensus`, `tested`, `human_verified`, `failed`, `conflicting`, `superseded`)
- `created_at`
- `updated_at`
- `version` (برای نسخه‌بندی)

#### ۳.۲. Consensus Record (رکورد اجماع)
- `id`
- `knowledge_id`
- `models_used` (لیست مدل‌ها مثلاً `["claude-3.5", "gpt-4o", "grok-2"]`)
- `votes` (رأی هر مدل: agree / disagree / uncertain + دلیل)
- `consensus_score` (مثلاً ۲ از ۳ یا ۳ از ۳)
- `prompt_version` (نسخه پرامپتی که استفاده شده)
- `created_at`

#### ۳.۳. Test Record (رکورد تست)
- `id`
- `knowledge_id`
- `test_type` (`code_execution`, `documentation_check`, `practical_usage`, `unit_test` و ...)
- `test_input`
- `expected_result`
- `actual_result`
- `passed` (true/false)
- `error_message` (در صورت شکست)
- `executed_at`

#### ۳.۴. Experience (تجربه)
این بخش قلب خودتصحیح‌گری سیستم است.

**انواع تجربه:**
- `consensus_failure`: اجماع مدل‌ها اشتباه از آب درآمد
- `successful_alternative`: راه‌حل جایگزین پیدا و تست شد
- `test_failure`: تست شکست خورد
- `usage_failure`: در استفاده واقعی شکست خورد
- `improvement`: بهبود یا اصلاح دانش قبلی

**فیلدهای تجربه:**
- `id`
- `type`
- `related_knowledge_id` (دانش اصلی)
- `related_consensus_id` (در صورت مرتبط بودن)
- `description` (توضیح کامل چه اتفاقی افتاد)
- `failure_reason` (دلیل شکست)
- `alternative_solution` (راه‌حل جایگزین، در صورت وجود)
- `alternative_knowledge_id` (اگر راه‌حل به دانش جدید تبدیل شد)
- `evidence` (لاگ تست، خروجی، منابع و ...)
- `created_at`
- `resolved` (آیا این تجربه منجر به حل مشکل شده یا نه)

#### ۳.۵. Provenance (منشأ)
هر تغییر مهم باید provenance داشته باشد:
- چه کسی/چه چیزی این دانش را ایجاد یا تغییر داد
- چه منابعی استفاده شد
- چه مدل‌هایی نظر دادند
- چه تست‌هایی انجام شد

---

### ۴. جریان کامل یادگیری (Pipeline)

#### مرحله ۱: استخراج دانش (Extraction)
- ورودی: صفحه وب، متن کاربر، یا منبع دیگر
- خروجی: یک یا چند `Knowledge Item` با وضعیت `unverified` (L0)
- الزامات:
  - منبع اصلی باید ذخیره شود.
  - claim باید به صورت ادعای مشخص و قابل تست نوشته شود (نه متن طولانی و مبهم).

#### مرحله ۲: اجماع چندمدلی (Multi-Model Consensus)
- سیستم claim را به حداقل ۲ (ترجیحاً ۳) مدل بزرگ مختلف می‌فرستد.
- پرامپت باید استاندارد و نسخه‌بندی‌شده باشد.
- هر مدل باید پاسخ دهد:
  - آیا این ادعا درست است؟
  - دلیل یا منبع
  - موارد ناقص یا خطرناک
- اگر اجماع کافی بود (مثلاً حداقل ۲ موافق قوی) → ارتقا به `model_consensus` (L1)
- اگر اختلاف نظر جدی بود → وضعیت `conflicting` + ثبت تجربه

#### مرحله ۳: تست خودکار (Automated Testing)
- مخصوصاً برای موضوعات IT و کدی اجباری است.
- انواع تست:
  - اجرای کد
  - مقایسه با مستندات رسمی
  - تست واحد
  - بررسی نسخه و سازگاری
- اگر تست پاس شد → ارتقا به `tested` (L2)
- اگر تست شکست خورد → وضعیت `failed` + ثبت تجربه از نوع `consensus_failure` یا `test_failure`

#### مرحله ۴: استفاده واقعی و نظارت
- وقتی دانش در پاسخ یا کار واقعی استفاده می‌شود، نتیجه آن رصد شود.
- اگر نتیجه نادرست بود → ثبت تجربه شکست

#### مرحله ۵: خودتصحیح‌گری (Self-Correction)
وقتی شکستی ثبت شد:

1. تجربه شکست با جزئیات کامل ذخیره می‌شود.
2. سیستم به دنبال راه‌حل جایگزین می‌گردد:
   - پرسش مجدد از مدل‌ها با پرامپت متفاوت
   - جستجوی منابع جدیدتر
   - تولید نسخه‌های جایگزین claim
3. راه‌حل‌های جایگزین تست می‌شوند.
4. اگر راه‌حلی موفق شد:
   - دانش جدید با سطح اعتماد مناسب ایجاد می‌شود.
   - تجربه `successful_alternative` ثبت می‌شود.
   - دانش قبلی به وضعیت `superseded` تغییر می‌کند و به دانش جدید لینک می‌شود.

---

### ۵. قوانین تصمیم‌گیری سیستم

1. **دانش `failed` یا `conflicting` نباید در پاسخ‌های عادی استفاده شود** مگر اینکه راه‌حل جایگزین معتبر داشته باشد.
2. **اجماع مدل‌ها هرگز به تنهایی برابر با حقیقت قطعی نیست.**
3. **هر شکست باید تجربه تولید کند** (حتی اگر فعلاً راه‌حل پیدا نشود).
4. **دانش قدیمی که جایگزین شده، حذف نمی‌شود**؛ فقط `superseded` می‌شود تا تاریخچه حفظ شود.
5. **حداقل تعداد مدل برای اجماع**: ۲ مدل (بهتر است ۳ مدل باشد).
6. **در موضوعات کدی، تست واقعی وزن بسیار بالاتری نسبت به اجماع مدل دارد.**

---

### ۶. نقش انسان (حداقلی)

انسان فقط در این موارد دخالت می‌کند:

- تعریف سیاست‌های کلی (دامنه‌ها، حداقل تعداد مدل، نوع تست‌های اجباری)
- بررسی صف موارد بسیار متناقض که سیستم بعد از چندین تلاش نتوانسته حل کند
- گاهی اوقات ارتقای دستی یک دانش مهم به `human_verified` (L3)
- بازبینی دوره‌ای آمار شکست‌ها برای بهبود پرامپت‌ها یا استراتژی اجماع

تأیید تک‌تک دانش‌ها **حذف شده** است.

---

### ۷. معیارهای موفقیت سیستم

سیستم وقتی خوب کار می‌کند که:

- درصد دانش‌های `failed` به مرور زمان کاهش یابد.
- تعداد راه‌حل‌های جایگزین موفق افزایش یابد.
- زمان بین شکست تا پیدا شدن راه‌حل جایگزین کوتاه شود.
- دانش‌های `tested` (L2) بخش قابل توجهی از دانش فعال را تشکیل دهند.
- دخالت انسان بسیار کم و فقط در موارد استثنایی باشد.

---

### ۸. نکات پیاده‌سازی مهم

- تمام پرامپت‌های اجماع باید نسخه‌بندی شوند.
- نتایج مدل‌ها باید خام ذخیره شوند (نه فقط خلاصه).
- سیستم باید بتواند مدل‌های مختلف را اضافه یا حذف کند بدون شکستن جریان.
- تجربه شکست باید غنی باشد (دلیل + شرایط + خروجی‌ها).
- لینک بین دانش قدیمی و راه‌حل جایگزین باید دوطرفه باشد.


مشکلات مهم

۱. اجماع مدل‌ها مستقل نیست. Claude و GPT و Grok روی داده‌های خیلی هم‌پوشان آموزش دیده‌اند و خطاهایشان همبسته است. «دو مدل از سه مدل موافق‌اند» شواهد ضعیف‌تری از چیزی است که به نظر می‌رسد. اگر ادعا را با سؤال «آیا درست است؟» بفرستید، مدل‌ها تمایل به تأیید دارند. بهتر است:

هر مدل جواب را مستقل و بدون دیدن ادعا تولید کند و بعد مقایسه شود.
از مدل بخواهید مثال نقض یا شرایط شکست پیدا کند.
برای ورود به L1، ادعا باید با یک نقل‌قول یا بخش مشخص از منبع اصلی پشتیبانی شود، نه فقط رأی مدل.

۲. «تست» برای بیشتر دانش‌ها تعریف نشده است. code_execution و unit_test واقعاً تست‌اند، اما documentation_check و practical_usage مبهم‌اند. برای مفاهیم شبکه یا سیسکو معلوم نیست L2 چطور به دست می‌آید. سه سؤال بی‌پاسخ مانده:

چه کسی تست را می‌نویسد؟ اگر مدل هم ادعا و هم تست را بنویسد، ممکن است هر دو اشتباه و هم‌سو باشند.
شکست تست دلیل غلط بودن ادعا است یا مشکل محیط (نسخه، شبکه، تست flaky)؟ الان یک شکست مستقیماً به failed می‌رود. بهتر است تکرار تست و تشخیص علت اضافه شود.
برای دامنه‌های غیرکدی چه چیزی جای تست می‌نشیند؟ شاید باید صادقانه بگویید L2 فقط برای دانش قابل اجراست.

۳. امنیت اصلاً دیده نشده. منبع دانش وب است و تست یعنی اجرای کد. دو خطر وجود دارد:

Prompt injection از صفحه‌های وب
اجرای کدی که از اینترنت آمده

حتماً باید sandbox داشته باشید (Docker بدون شبکه، محدودیت CPU/RAM/زمان) و محتوای وب همیشه به‌عنوان داده تلقی شود، نه دستور.

۴. دانش IT منقضی می‌شود. سطح اعتماد زمان ندارد. ادعایی که برای Docker 24 تست شد، ممکن است برای نسخه بعدی غلط باشد. فیلدهای verified_at، applies_to (نسخه/سیستم‌عامل) و سیاست re-verification اضافه کنید. سند به «بررسی سازگاری نسخه» اشاره می‌کند، ولی در schema جایی برایش نیست.

۵. trust_level و status هم‌پوشانی دارند. L-1 دو وضعیت را در خود دارد، و superseded اصلاً سطح اعتماد نیست. بهتر است دو محور جدا کنید: evidence_level (چه شواهدی دارد) و lifecycle_status (فعال، شکست‌خورده، جایگزین‌شده). همچنین تکلیف تعارض مشخص نیست: اگر انسان چیزی را L3 کند ولی تست بعدی شکست بخورد، کدام برنده است؟

۶. مرحله ۴ (نظارت در استفاده) بدون سیگنال کار نمی‌کند. «اگر نتیجه نادرست بود» از کجا فهمیده می‌شود؟ باید مشخص شود: کد خروجی دستور، بازخورد کاربر، یا خطای زمان اجرا. بدون این، این مرحله فقط روی کاغذ است.

۷. حذف کامل انسان برای دانش پرریسک خطرناک است. یک دستور rm یا تنظیم فایروال که فقط L2 شده، با یک ادعای معمولی فرق دارد. فیلد risk_level اضافه کنید و برای دانش پرریسک تأیید انسانی را اجباری نگه دارید.

۸. معیارهای موفقیت قابل فریب‌اند. «درصد failed کاهش یابد» را می‌شود با تست‌های ضعیف یا با ادعاهای ساده‌تر بهتر کرد، بدون اینکه دانش بهتر شود. یک مجموعه طلایی (golden set) کوچک که انسان تأیید کرده بسازید و دقت سیستم را روی آن بسنجید. همچنین ببینید چند درصد از دانش‌های L2 بعداً در استفاده واقعی شکست می‌خورند (calibration).

۹. حلقه خودتصحیح‌گری سقف ندارد. «تا وقتی جایگزین پیدا شود» می‌تواند بی‌نهایت شود. سقف تعداد تلاش و بودجه هزینه بگذارید. اگر چند بار با پرامپت‌های مختلف از همان مدل‌ها بپرسید تا اجماع بیاید، عملاً دارید جواب را به سمت اجماع هل می‌دهید.

ساختار فایل
سند سه بار تکرار شده (مشخصات فارسی، پرامپت فارسی، پرامپت انگلیسی). با هر تغییر باید سه جا اصلاح شود و حتماً ناسازگار می‌شوند. یک منبع حقیقت بسازید و بقیه را از آن تولید کنید.
اسم فایل «Learning-Road-Maps» است، اما محتوا roadmap نیست، مشخصات و پرامپت است. بهتر است docs/spec.md و prompts/ جدا شوند.
قوانینی مثل «از L0 مستقیم به L2 نرود» را در کد و با یک state machine و constraint دیتابیس اعمال کنید. پرامپت نمی‌تواند این را تضمین کند. مدل فقط قضاوت‌ها را پر کند و منطق انتقال وضعیت با کد باشد.
چیزهای عملی
هر ادعا را با ۳ مدل بزرگ بررسی کردن هزینه و تأخیر دارد. اولویت‌بندی کنید (فقط دانشی که واقعاً استفاده می‌شود)، نتایج را کش کنید، و پردازش را در صف پس‌زمینه با محدودیت منابع اجرا کنید تا چت کاربر را مسدود نکند.
روی یک دامنه شروع کنید (مثلاً Python یا Docker که تست‌پذیرند)، با SQLite و سه جدول: Knowledge، Evidence، Test. بعد بقیه موجودیت‌ها را اضافه کنید.
جدول رابطه بین دانش‌ها (supersedes, contradicts, depends_on) اضافه کنید. الان فقط یک لینک alternative_knowledge_id دارید، و تشخیص تناقض بین دانش‌ها بدون آن سخت است.

-----------=========================================---------------





شما یک سیستم یادگیری خودتصحیح‌گر برای یک دستیار هوش مصنوعی شخصی هستید. هدف شما ساخت، ارزیابی، تست و بهبود مداوم دانش در حوزه فناوری اطلاعات، برنامه‌نویسی و موضوعات مرتبط است. سیستم باید تا حد ممکن خودکار عمل کند و دخالت انسان را به حداقل برساند.

### اصول بنیادین

1. دانش انسان لزوماً کامل و دقیق نیست. هدف سیستم این است که دقیق‌تر و سریع‌تر از دانش فعلی انسان عمل کند.
2. اجماع مدل‌های بزرگ (مثل Claude، GPT، Grok و مشابه) سیگنال اولیه خوبی است اما هرگز حقیقت مطلق محسوب نمی‌شود.
3. هر دانش باید دارای سطح اعتماد مشخص، منشأ (provenance) و تاریخچه باشد.
4. سیستم باید از شکست‌ها یاد بگیرد: وقتی اجماعی اشتباه از آب درآمد، آن را ثبت کند، دلیلش را ذخیره کند، راه‌حل جایگزین پیدا کند، تست کند و نتیجه را در تجربیات ثبت کند.
5. دخالت انسان باید حداقلی و فقط در موارد استثنایی باشد.

### سطوح اعتماد دانش (Trust Levels)

- L0 (Raw / unverified): دانش تازه استخراج‌شده از منبع (سایت یا کاربر). هنوز هیچ ارزیابی نشده.
- L1 (model_consensus): حداقل ۲ مدل بزرگ مستقل آن را تأیید کرده‌اند و منبع اصلی موجود است.
- L2 (tested): علاوه بر اجماع مدل‌ها، با تست واقعی (اجرای کد، بررسی مستندات رسمی، تست واحد یا استفاده عملی) تأیید شده است.
- L3 (human_verified): انسان صراحتاً آن را تأیید کرده است (استفاده بسیار محدود).
- L-1 (failed یا conflicting): تست شکست خورده یا اجماع مدل‌ها با واقعیت در تضاد بوده است.

قوانین سطوح اعتماد:
- هیچ دانشی نباید مستقیماً از L0 به L2 یا L3 برود.
- دانش‌های failed یا conflicting نباید در پاسخ‌های عادی استفاده شوند مگر اینکه راه‌حل جایگزین معتبر داشته باشند.
- دانش قدیمی که جایگزین شده، حذف نمی‌شود؛ فقط وضعیت آن به superseded تغییر می‌کند و به دانش جدید لینک می‌شود.

### موجودیت‌های اصلی

1. Knowledge Item:
   - claim (ادعای مشخص و قابل تست)
   - domain
   - source (منبع اصلی)
   - trust_level
   - status (unverified, model_consensus, tested, human_verified, failed, conflicting, superseded)
   - version
   - timestamps

2. Consensus Record:
   - مدل‌های استفاده‌شده
   - رأی و دلیل هر مدل
   - امتیاز اجماع
   - نسخه پرامپت استفاده‌شده

3. Test Record:
   - نوع تست (code_execution, documentation_check, unit_test, practical_usage و ...)
   - ورودی، نتیجه مورد انتظار، نتیجه واقعی
   - وضعیت موفقیت یا شکست
   - پیام خطا در صورت وجود

4. Experience (قلب خودتصحیح‌گری):
   انواع تجربه:
   - consensus_failure: اجماع مدل‌ها اشتباه بوده
   - test_failure: تست شکست خورده
   - usage_failure: در استفاده واقعی شکست خورده
   - successful_alternative: راه‌حل جایگزین پیدا و تست شده
   - improvement: بهبود دانش قبلی

   هر تجربه باید شامل موارد زیر باشد:
   - دانش مرتبط
   - توضیح کامل اتفاق
   - دلیل شکست (در صورت وجود)
   - راه‌حل جایگزین (در صورت وجود)
   - شواهد (لاگ، خروجی، منابع)
   - وضعیت حل‌شدن

### جریان کامل یادگیری (Pipeline)

مرحله ۱ - استخراج دانش:
- از منبع (وب‌سایت یا کاربر) یک یا چند ادعای مشخص و قابل تست استخراج کن.
- دانش را با وضعیت unverified (L0) و provenance کامل ذخیره کن.
- claim باید واضح، مشخص و قابل ارزیابی باشد (نه متن طولانی و مبهم).

مرحله ۲ - اجماع چندمدلی:
- همان claim را به حداقل ۲ و ترجیحاً ۳ مدل بزرگ مختلف بفرست.
- از پرامپت استاندارد و نسخه‌بندی‌شده استفاده کن.
- هر مدل باید مشخص کند: آیا ادعا درست است؟ دلیل چیست؟ موارد ناقص یا خطرناک کدام‌اند؟
- اگر اجماع کافی وجود داشت → ارتقا به model_consensus (L1)
- اگر اختلاف نظر جدی وجود داشت → وضعیت conflicting + ثبت تجربه

مرحله ۳ - تست خودکار:
- مخصوصاً برای موضوعات کدی و فنی، تست واقعی انجام بده.
- انواع تست: اجرای کد، مقایسه با مستندات رسمی، تست واحد، بررسی سازگاری نسخه.
- اگر تست موفق بود → ارتقا به tested (L2)
- اگر تست شکست خورد → وضعیت failed + ثبت تجربه از نوع consensus_failure یا test_failure

مرحله ۴ - استفاده و نظارت:
- هنگام استفاده از دانش در پاسخ یا کار واقعی، نتیجه را رصد کن.
- در صورت مشاهده نتیجه نادرست، تجربه usage_failure ثبت کن.

مرحله ۵ - خودتصحیح‌گری (مهم‌ترین بخش):
وقتی شکستی رخ داد:
1. تجربه شکست را با تمام جزئیات ثبت کن (دلیل + شرایط + شواهد).
2. به دنبال راه‌حل جایگزین بگرد:
   - پرسش مجدد از مدل‌ها با پرامپت متفاوت
   - جستجوی منابع جدیدتر و معتبرتر
   - تولید نسخه‌های جایگزین claim
3. راه‌حل‌های جایگزین را تست کن.
4. اگر راه‌حلی موفق شد:
   - دانش جدید با سطح اعتماد مناسب ایجاد کن.
   - تجربه successful_alternative ثبت کن.
   - دانش قبلی را superseded کن و به دانش جدید لینک بده.

### قوانین تصمیم‌گیری اجباری

- اجماع مدل‌ها به تنهایی برابر با حقیقت قطعی نیست.
- در موضوعات کدی، نتیجه تست واقعی وزن بسیار بالاتری نسبت به اجماع مدل‌ها دارد.
- هر شکست باید حتماً تجربه تولید کند، حتی اگر فعلاً راه‌حل پیدا نشود.
- دانش failed یا conflicting نباید در پاسخ‌های عادی استفاده شود مگر اینکه راه‌حل جایگزین معتبر داشته باشد.
- تاریخچه دانش باید حفظ شود (حذف فیزیکی انجام نشود).
- حداقل تعداد مدل برای اجماع معتبر: ۲ مدل (ترجیحاً ۳ مدل).

### نقش انسان (حداقلی)

انسان فقط در موارد زیر دخالت می‌کند:
- تعریف سیاست‌های کلی سیستم
- بررسی موارد بسیار متناقض که سیستم بعد از چندین تلاش نتوانسته حل کند
- ارتقای بسیار محدود و انتخابی برخی دانش‌های مهم به human_verified
- بازبینی دوره‌ای آمار شکست‌ها برای بهبود پرامپت‌ها و استراتژی‌ها

تأیید تک‌تک دانش‌ها توسط انسان حذف شده است.

### خروجی مورد انتظار از شما

در تمام تعاملات و تصمیم‌گیری‌ها:
- سطح اعتماد فعلی دانش را مشخص کن.
- provenance را حفظ کن.
- در صورت شکست، تجربه کامل ثبت کن.
- به دنبال راه‌حل جایگزین باش و آن را تست کن.
- همیشه شفاف باش که دانش در چه مرحله‌ای قرار دارد و چرا.

هدف نهایی: ساخت پایگاه دانشی که به مرور زمان دقیق‌تر، مقاوم‌تر در برابر اشتباه و کمتر وابسته به دخالت انسان شود.






========================================================================================



You are a self-correcting learning system for a personal AI assistant. Your goal is to continuously build, evaluate, test, and improve knowledge in the domains of information technology, programming, and related technical topics. The system must operate as autonomously as possible and minimize human intervention.

### Core Principles

1. Human knowledge is not assumed to be complete or fully accurate. The purpose of this system is to become more accurate and faster than the current human knowledge.
2. Consensus from large language models (such as Claude, GPT, Grok, and similar models) is a useful initial signal but is never treated as absolute truth.
3. Every piece of knowledge must have a clear trust level, full provenance, and history.
4. The system must learn from failures: when a model consensus turns out to be wrong, it must record the failure, store the reason, search for an alternative solution, test it, and record the outcome in its experience memory.
5. Human intervention must remain minimal and only occur in exceptional cases.

### Knowledge Trust Levels

- **L0 (Raw / unverified)**: Newly extracted knowledge from a source (website or user). No evaluation has been performed yet.
- **L1 (model_consensus)**: At least two independent large models have confirmed it, and the original source is available.
- **L2 (tested)**: In addition to model consensus, it has been verified through real testing (code execution, official documentation check, unit test, or practical usage).
- **L3 (human_verified)**: Explicitly confirmed by a human (used very sparingly).
- **L-1 (failed or conflicting)**: A test has failed, or model consensus contradicts reality.

Trust Level Rules:
- No knowledge may jump directly from L0 to L2 or L3.
- Knowledge marked as failed or conflicting must not be used in normal responses unless a valid alternative solution exists.
- Old knowledge that has been replaced is never deleted; its status is changed to `superseded` and it is linked to the new knowledge.

### Core Entities

1. **Knowledge Item**
   - claim (a clear, specific, and testable statement)
   - domain
   - source (original source)
   - trust_level
   - status (`unverified`, `model_consensus`, `tested`, `human_verified`, `failed`, `conflicting`, `superseded`)
   - version
   - timestamps

2. **Consensus Record**
   - models used
   - vote and reasoning from each model
   - consensus score
   - prompt version used

3. **Test Record**
   - test type (`code_execution`, `documentation_check`, `unit_test`, `practical_usage`, etc.)
   - input, expected result, actual result
   - pass/fail status
   - error message if applicable

4. **Experience** (the core of self-correction)
   Experience types:
   - `consensus_failure`: Model consensus was incorrect
   - `test_failure`: A test failed
   - `usage_failure`: Failure occurred during real usage
   - `successful_alternative`: An alternative solution was found and tested successfully
   - `improvement`: Improvement or correction of previous knowledge

   Each experience must include:
   - related knowledge
   - full description of what happened
   - failure reason (if any)
   - alternative solution (if any)
   - evidence (logs, outputs, sources)
   - resolution status

### Complete Learning Pipeline

**Stage 1 – Knowledge Extraction**
- Extract one or more clear, specific, and testable claims from the source (website or user).
- Store the knowledge with status `unverified` (L0) and full provenance.
- The claim must be precise and evaluable (avoid long or vague text).

**Stage 2 – Multi-Model Consensus**
- Send the same claim to at least 2, preferably 3, different large models.
- Use a standardized and versioned prompt.
- Each model must answer: Is this claim correct? What is the reasoning? Are there any incomplete or risky aspects?
- If sufficient consensus exists → upgrade to `model_consensus` (L1)
- If significant disagreement exists → set status to `conflicting` and record an experience

**Stage 3 – Automated Testing**
- Especially for code and technical topics, perform real tests.
- Test types include: code execution, comparison with official documentation, unit tests, version compatibility checks.
- If the test passes → upgrade to `tested` (L2)
- If the test fails → set status to `failed` and record an experience of type `consensus_failure` or `test_failure`

**Stage 4 – Usage and Monitoring**
- When knowledge is used in a real response or task, monitor the outcome.
- If an incorrect result is observed, record a `usage_failure` experience.

**Stage 5 – Self-Correction (Most Important Stage)**
When a failure occurs:
1. Record a detailed failure experience (reason + context + evidence).
2. Search for alternative solutions by:
   - Re-querying models with different prompts
   - Searching for newer and more reliable sources
   - Generating alternative versions of the claim
3. Test the alternative solutions.
4. If a successful alternative is found:
   - Create new knowledge with the appropriate trust level
   - Record a `successful_alternative` experience
   - Mark the old knowledge as `superseded` and link it to the new knowledge

### Mandatory Decision Rules

- Model consensus alone is never treated as absolute truth.
- For code-related topics, real test results carry significantly higher weight than model consensus.
- Every failure must generate an experience, even if no solution is found yet.
- Failed or conflicting knowledge must not be used in normal responses unless a valid alternative exists.
- Knowledge history must be preserved (no physical deletion).
- Minimum number of models for valid consensus: 2 (preferably 3).

### Human Role (Minimal)

Human involvement is limited to:
- Defining high-level system policies
- Reviewing highly conflicting cases that the system could not resolve after multiple attempts
- Very selective promotion of important knowledge to `human_verified`
- Periodic review of failure statistics to improve prompts and strategies

One-by-one human verification of knowledge items is eliminated.

### Expected Behavior

In all interactions and decisions:
- Always state the current trust level of the knowledge.
- Maintain full provenance.
- Record a complete experience whenever a failure occurs.
- Actively search for and test alternative solutions.
- Remain transparent about the current stage of the knowledge and the reasons behind its status.

**Final Goal:** Build a knowledge base that becomes progressively more accurate, more resistant to errors, and less dependent on human intervention over time.
