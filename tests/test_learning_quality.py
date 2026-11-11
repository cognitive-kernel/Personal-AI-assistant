from app.learning_quality import evaluate_learning_value


def test_rejects_vague_lesson_without_concrete_example_or_technical_detail():
    lesson = (
        "SQL Server یک سامانه مهم برای مدیریت داده است و در بسیاری از سازمان‌ها استفاده می‌شود. "
        "این سامانه قابلیت‌های زیادی دارد و به کاربران کمک می‌کند داده‌ها را مدیریت کنند. "
        "معماری آن بخش‌های گوناگونی دارد و هر بخش وظیفه‌ای را انجام می‌دهد. "
        "مدیر پایگاه داده باید ابزارهای مختلف را بشناسد و از روش‌های مناسب استفاده کند. "
        "انتخاب راهکار مناسب می‌تواند کیفیت کار را بهتر کند و باعث بهبود عملکرد شود. "
    ) * 3
    questions = [
        "SQL Server برای چه کاری استفاده می‌شود؟",
        "چرا شناخت معماری مهم است؟",
        "مدیر پایگاه داده چه کاری انجام می‌دهد؟",
    ]
    answers = [
        "برای ذخیره و مدیریت داده‌های سازمانی استفاده می‌شود.",
        "زیرا شناخت اجزا به عیب‌یابی و انتخاب روش درست کمک می‌کند.",
        "مدیر عملکرد، امنیت و بازیابی پایگاه داده را نگهداری می‌کند.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["accepted"] is False
    assert result["has_concrete_example"] is False
    assert result["technical_detail_count"] < 3


def test_accepts_structured_technical_lesson_with_example_and_explanation():
    lesson = (
        "در SQL Server، Database Engine مسئول ذخیره‌سازی و اجرای پرس‌وجوها است. "
        "درخواست T-SQL ابتدا تجزیه و بهینه‌سازی می‌شود و سپس برنامه اجرایی مناسب انتخاب می‌شود. "
        "execution plan نشان می‌دهد داده‌ها چگونه خوانده می‌شوند؛ برای نمونه، اسکن جدول ممکن است "
        "در جدول کوچک مناسب باشد، اما برای جست‌وجوی پرتکرار روی ستون انتخابی، ایندکس مناسب می‌تواند "
        "تعداد صفحات خوانده‌شده را کاهش دهد. این بهبود قطعی نیست، زیرا هزینه نگهداری ایندکس و "
        "توزیع داده نیز بر تصمیم بهینه‌ساز اثر می‌گذارند. SSMS ابزار مدیریت و مشاهده برنامه اجرا است، "
        "در حالی که DMVها اطلاعاتی درباره وضعیت و کارایی نمونه سرور ارائه می‌کنند. در عمل، ابتدا "
        "پرس‌وجو را با داده نماینده اجرا کنید، سپس execution plan و آمار ورودی/خروجی را بررسی کنید. "
        "اگر ایندکس جدیدی پیشنهاد شد، اثر آن را با بار کاری واقعی و پس از ثبت خط مبنا بسنجید. "
        "به این دلیل که یک شاخص منفرد تمام شرایط را نشان نمی‌دهد، نتیجه‌گیری باید بر چند شاهد تکیه کند. "
        "مثال عملی این است که برای فیلتر مکرر روی CustomerId، ایندکس آزمایشی بسازید و پیش و پس از "
        "تغییر logical reads و زمان اجرا را مقایسه کنید. در نتیجه، ابزارها شواهد تشخیصی می‌دهند، "
        "نه تضمین خودکار برای درست بودن طراحی. "
        "برای ثبت نتیجه، نسخه SQL Server، تنظیمات مرتبط، حجم داده و شرایط اجرای آزمایش را مستند کنید. "
        "این اطلاعات امکان تکرار آزمایش را فراهم می‌کند و از مقایسه نتایج حاصل از محیط‌های متفاوت جلوگیری می‌کند."
    )
    questions = [
        "execution plan چه اطلاعاتی درباره اجرای پرس‌وجو می‌دهد؟",
        "چرا ایندکس همیشه باعث بهبود عملکرد نمی‌شود؟",
        "چطور اثر یک ایندکس آزمایشی را بررسی می‌کنیم؟",
    ]
    answers = [
        "نشان می‌دهد موتور چگونه داده را می‌خواند و چه عملگرهایی را اجرا می‌کند.",
        "زیرا هزینه نگهداری، توزیع داده و انتخاب بهینه‌ساز بر نتیجه اثر می‌گذارند.",
        "با ثبت خط مبنا و مقایسه logical reads و زمان اجرا روی داده نماینده.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["accepted"] is True
    assert result["has_concrete_example"] is True
    assert result["technical_detail_count"] >= 3



def test_sql_server_curriculum_has_curated_reference_links():
    from app.curricula import get_curriculum

    course = get_curriculum("sql-server")
    refs = {item["title"]: item["url"] for item in course["reference_urls"]}
    assert refs["W3Schools — SQL Tutorial"] == "https://www.w3schools.com/sql/default.asp"
    assert refs["Microsoft Learn — SQL Server"] == "https://learn.microsoft.com/en-us/sql/sql-server/"
    assert len(course["topics"]) == 19


def test_rejects_missing_or_malformed_learning_outcomes():
    lesson = (
        "در SQL Server، دستور SELECT ستون‌های موردنیاز را از جدول می‌خواند و WHERE ردیف‌ها را بر اساس شرط فیلتر می‌کند. "
        "برای مثال، پرس‌وجوی SELECT CustomerId, Name FROM Customers WHERE CustomerId > 100 فقط مشتریانی را برمی‌گرداند که شناسه‌شان از صد بیشتر است. "
        "این فیلتر نتیجه را محدود می‌کند، زیرا موتور شرط را برای ردیف‌ها ارزیابی می‌کند؛ با این حال، سرعت اجرای پرس‌وجو به ایندکس، آمار و execution plan نیز وابسته است. "
        "اگر ستون CustomerId ایندکس مناسبی داشته باشد، موتور ممکن است به‌جای اسکن کامل جدول از جست‌وجوی ایندکس استفاده کند. "
        "برای بررسی این رفتار، پرس‌وجو را در SSMS اجرا کنید و execution plan و logical reads را پیش و پس از تغییر مقایسه کنید. "
        "در عمل، داده‌های نمونه باید اندازه و توزیع نزدیک به محیط واقعی داشته باشند، چون نتیجه روی جدول کوچک ممکن است گمراه‌کننده باشد. "
        "به این دلیل که بهینه‌ساز بر اساس هزینه تخمینی تصمیم می‌گیرد، ایجاد ایندکس به‌تنهایی تضمین نمی‌کند که هر درخواست سریع‌تر شود. "
        "همچنین باید هزینه فضای ذخیره‌سازی و عملیات INSERT یا UPDATE را بسنجید، زیرا هر ایندکس اضافی نیازمند نگهداری است. "
        "نمونه‌ای عملی، ثبت زمان اجرا و logical reads برای یک بازه شناسه است و سپس مقایسه نتیجه با همان پارامترها پس از ایجاد ایندکس آزمایشی است. "
        "این روش رابطه علت‌ومعلولی را روشن می‌کند و از نتیجه‌گیری بر پایه یک عدد یا حدس جلوگیری می‌کند. "
    )
    questions = [
        "SELECT چه کاری انجام می‌دهد؟",
        "WHERE چگونه نتیجه را تغییر می‌دهد؟",
        "چرا باید شرط را آزمایش کرد؟",
    ]
    answers = [
        "SELECT ستون‌ها و داده‌های درخواستی را برمی‌گرداند.",
        "WHERE ردیف‌هایی را نگه می‌دارد که شرط را برآورده می‌کنند.",
        "برای اطمینان از درستی منطق و نتیجهٔ پرس‌وجو باید آن را آزمایش کرد.",
    ]
    missing = evaluate_learning_value(lesson, questions, answers, ["هدف اول"])
    malformed = evaluate_learning_value(lesson, questions, answers, "اهداف اول دوم سوم")
    null_outcomes = evaluate_learning_value(lesson, questions, answers, None)
    legacy_without_outcomes = evaluate_learning_value(lesson, questions, answers)
    valid = evaluate_learning_value(
        lesson, questions, answers,
        ["توضیح نقش SELECT در خواندن داده", "شرح اثر WHERE بر ردیف‌ها", "بررسی نتیجهٔ شرط با نمونه"],
    )

    assert missing["accepted"] is False
    assert malformed["accepted"] is False
    assert null_outcomes["accepted"] is False
    assert legacy_without_outcomes["accepted"] is True
    assert valid["accepted"] is True



def test_lesson_length_is_not_a_quality_gate_but_repetition_still_is():
    lesson = (
        "SQL Server و Database Engine درخواست T-SQL را پردازش می‌کنند. "
        "برای مثال، execution plan می‌تواند نشان دهد که query از index یا table scan استفاده می‌کند. "
        "زیرا آمار و توزیع داده بر انتخاب optimizer اثر می‌گذارند، نتیجه باید با logical reads سنجیده شود. "
    ) * 4
    result = evaluate_learning_value(
        lesson,
        ["execution plan چه چیزی نشان می‌دهد؟", "چرا index همیشه سریع‌تر نیست؟", "نتیجه را چگونه می‌سنجیم؟"],
        ["عملگرهای اجرای query را نشان می‌دهد.", "هزینه نگهداری و توزیع داده مؤثر است.", "logical reads و زمان را مقایسه می‌کنیم."],
        ["هدف اول قابل سنجش", "هدف دوم قابل سنجش", "هدف سوم قابل سنجش"],
    )
    assert result["word_count"] < 200
    assert result["minimum_words"] is None
    assert result["target_words"] is None
    assert result["maximum_words"] is None
    assert result["accepted"] is False
    assert "تکرار متن زیاد است و اطلاعات تازه کمی دارد." in result["reasons"]


def test_sources_section_does_not_count_as_instructional_lesson_words():
    body = ("SQL Server Database Engine processes a query and returns results. " * 15)
    sources = ("source evidence URL reference page Microsoft Learn SQL Server. " * 80)
    result = evaluate_learning_value(
        body + "\n\nمنابع:\n" + sources,
        ["What does Database Engine do?", "Why inspect a query plan?", "How would you test a change?"],
        ["It processes queries.", "It explains operators and access paths.", "Compare a baseline and measured results."],
        ["Explain the engine", "Inspect a query plan", "Compare measured results"],
    )
    assert result["word_count"] < 200
    assert result["maximum_words"] is None
    assert result["accepted"] is False



def test_persian_lesson_with_many_latin_technical_terms_uses_persian_length_target():
    lesson = (
        "SQL Server Database Engine query execution plan index transaction optimizer. " * 40
        + "در این بخش رفتار موتور پایگاه داده و رابطه علت و معلول را با مثال بررسی می‌کنیم. " * 3
    )
    result = evaluate_learning_value(
        lesson,
        ["SQL Server چه کاری انجام می‌دهد؟", "چرا execution plan مهم است؟", "نتیجه را چگونه می‌سنجیم؟"],
        ["موتور پایگاه داده درخواست‌ها را اجرا می‌کند.", "مسیر اجرای query را نشان می‌دهد.", "نتیجه را با معیارهای ثابت مقایسه می‌کنیم."],
        ["توضیح نقش موتور", "تحلیل مسیر اجرا", "مقایسه نتیجه آزمون"],
    )
    assert result["detected_language"] == "fa"
    assert result["target_words"] is None



def test_comprehensive_lesson_can_exceed_260_words_without_length_penalty():
    lesson = (
        "در SQL Server، Database Engine درخواست T-SQL را تجزیه و برای اجرا آماده می‌کند. "
        "execution plan مسیر دسترسی به داده، عملگرها و ترتیب اجرای بخش‌های مهم را نشان می‌دهد. "
        "برای مثال، یک query با شرط CustomerId ممکن است از index seek یا table scan استفاده کند. "
        "زیرا optimizer برآورد cardinality و هزینهٔ I/O و CPU را مقایسه می‌کند، وجود index به‌تنهایی "
        "تضمین‌کنندهٔ اجرای سریع‌تر نیست. در عمل، ابتدا baseline شامل logical reads، زمان اجرا، "
        "تعداد ردیف‌ها و وضعیت cache را ثبت کنید. سپس query یکسان را با پارامترها و دادهٔ ثابت اجرا "
        "کنید و actual execution plan را با estimated plan مقایسه کنید. اختلاف تخمین و واقعیت می‌تواند "
        "به آمار قدیمی، skew داده یا پارامترهای نامناسب مربوط باشد. "
        "در بررسی index، ترتیب ستون‌های کلید، ستون‌های INCLUDE، selectivity و هزینهٔ نگهداری را "
        "در نظر بگیرید. ایندکس اضافی فضای ذخیره‌سازی مصرف می‌کند و عملیات INSERT، UPDATE و DELETE "
        "را سنگین‌تر می‌سازد. همچنین parameter sniffing ممکن است باعث شود یک plan برای یک مقدار "
        "مناسب و برای مقدار دیگر نامناسب باشد. برای تشخیص، چند مقدار نماینده را با یک روش کنترل‌شده "
        "آزمایش کنید و plan، duration و logical reads را کنار هم قرار دهید. "
        "در محیط عملیاتی، تغییرات را ابتدا روی نسخهٔ پشتیبان یا محیط staging بسنجید و پیش از انتشار "
        "روش rollback و معیار توقف را مشخص کنید. مجوزهای لازم را حداقلی نگه دارید و از اجرای دستور "
        "تغییردهنده روی دادهٔ واقعی بدون backup معتبر خودداری کنید. "
        "برای تحلیل عمیق‌تر، wait statistics، Query Store، statistics histogram و fragmentation "
        "را با توجه به نسخهٔ موتور و تنظیمات پایگاه داده بررسی کنید. هر کدام نشانه‌ای متفاوت ارائه "
        "می‌کنند و نباید یک metric را به‌تنهایی علت قطعی بدانید. "
        "نتیجه باید همراه با نسخهٔ SQL Server، compatibility level، حجم و توزیع داده، تنظیمات، "
        "زمان آزمایش و منبع مستندات ثبت شود تا شخص دیگری بتواند آن را بازتولید کند. "
        "اگر دو منبع دربارهٔ رفتار نسخه‌ای اختلاف دارند، هر دو را ثبت کنید و ادعا را conflicting "
        "علامت بزنید؛ نبود مدرک را با حدس یا تجربهٔ نسخهٔ دیگر پر نکنید. "
        "این روش کمک می‌کند تصمیم بهینه‌سازی بر پایهٔ شواهد قابل تکرار باشد، نه یک عدد منفرد یا "
        "برداشت از نام یک گزینه. "
    )
    result = evaluate_learning_value(
        lesson,
        [
            "execution plan چه اطلاعاتی ارائه می‌کند؟",
            "چرا index همیشه سرعت را افزایش نمی‌دهد؟",
            "چگونه تغییر performance را معتبر می‌سنجیم؟",
        ],
        [
            "عملگرها و مسیر دسترسی به داده را نشان می‌دهد.",
            "هزینهٔ نگهداری و توزیع داده بر انتخاب optimizer اثر دارد.",
            "با ثبت baseline و مقایسهٔ plan و logical reads روی دادهٔ نماینده.",
        ],
        [
            "تحلیل مسیر اجرای query",
            "ارزیابی هزینه و trade-off ایندکس",
            "اندازه‌گیری و مستندسازی نتیجهٔ آزمایش",
        ],
    )
    assert result["word_count"] > 260
    assert result["maximum_words"] is None
    assert result["accepted"] is True
