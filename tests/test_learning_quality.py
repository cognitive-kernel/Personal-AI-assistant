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
        "نه تضمین خودکار برای درست بودن طراحی."
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



def test_quality_filter_exposes_language_aware_word_targets():
    english_lesson = (
        "SQL Server executes a query using an execution plan. For example, "
        "an index can reduce reads for a selective query, but it also adds "
        "maintenance cost. Database administrators should compare logical reads "
        "and elapsed time before and after a change using representative data. "
        "The optimizer chooses operators based on statistics and estimated cost. "
        "A plan is evidence about execution, not a guarantee that the query is optimal."
    )
    questions = ["What does an execution plan show?", "Why can an index add cost?", "How should a change be checked?"]
    answers = ["It shows operators used to execute the query.", "Indexes require storage and maintenance.", "Compare reads and elapsed time on representative data."]
    result = evaluate_learning_value(english_lesson, questions, answers)
    assert result["target_word_count"] == 120
    assert result["minimum_word_count"] == 80


def test_rejects_malformed_sql_server_terminology_even_when_shape_checks_pass():
    lesson = (
        "در SQL Server، دستورات دیکشنری یک نمودار طراحی شده برای اجرای پرس‌وجو هستند. "
        "برای مثال، ادغامات استراتژیک و توزیع عملیاتی به بهبود اجرای برنامه‌ها کمک می‌کنند. "
        "این روش باعث می‌شود کارایی پایگاه داده بهتر شود و نتیجه در زمان کمتری برگردد. "
    ) * 4
    questions = [
        "دستورات دیکشنری در SQL Server چه چیزی را نشان می‌دهند؟",
        "ادغامات استراتژیک چگونه بر اجرا اثر می‌گذارند؟",
        "چرا باید کارایی پرس‌وجو را اندازه‌گیری کرد؟",
    ]
    answers = [
        "این اصطلاح در این بافت اصطلاح فنی معتبر و روشنی نیست.",
        "این عبارت نامفهوم است و سازوکار مشخصی را توصیف نمی‌کند.",
        "برای مقایسه نتیجه پیش و پس از تغییر روی داده نماینده.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["accepted"] is False
    assert result["malformed_phrases"]
