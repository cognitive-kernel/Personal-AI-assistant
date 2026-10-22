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
        "نتیجه را همراه با نسخه موتور، تنظیمات پایگاه داده و مشخصات داده ثبت کنید تا اجرای بعدی قابل مقایسه باشد. "
        "اگر شرایط آزمایش تغییر کند، تفاوت نتیجه را نباید فقط به ایندکس نسبت داد؛ ابتدا باید متغیرهای محیطی را کنترل کرد. همچنین برنامهٔ آزمون باید نتیجهٔ مورد انتظار را از پیش تعریف کند و همان پارامترها را در هر اجرا به کار ببرد. ثبت مقدارهای واقعی و شرایط اجرا کمک می‌کند نتیجهٔ ناموفق از خطای محیط یا دادهٔ نامعتبر تفکیک شود."
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



def test_lesson_below_target_length_is_not_accepted_even_if_other_signals_exist():
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
    assert result["target_words"] == 200
    assert result["accepted"] is False
    assert any("بازه هدف 200" in reason for reason in result["reasons"])


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
    assert result["accepted"] is False
