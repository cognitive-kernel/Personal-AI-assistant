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
        "این اطلاعات امکان تکرار آزمایش را فراهم می‌کند و از مقایسه نتایج حاصل از محیط‌های متفاوت جلوگیری می‌کند. "
        "گزارش نهایی باید روش آزمایش، محدودیت‌ها و داده‌های مشاهده‌شده را از فرضیه‌ها جدا کند. "
        "در این صورت تیم می‌تواند نتیجه را در محیط مشابه بازتولید کند و از تعمیم یک مشاهده محدود به همه سامانه‌ها پرهیز کند."
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
    )
    assert result["target_words"] == 200
    assert result["accepted"] is False
    assert any("بازه هدف 200" in reason for reason in result["reasons"])


def test_curriculum_references_are_resolvable_for_exact_topic_only():
    from app.curricula import get_reference_urls_for_topic

    refs = get_reference_urls_for_topic("معماری SQL Server، Database Engine و ابزارهای مدیریت")
    assert {item["url"] for item in refs} == {
        "https://www.w3schools.com/sql/default.asp",
        "https://learn.microsoft.com/en-us/sql/sql-server/",
    }
    assert get_reference_urls_for_topic("a user supplied arbitrary topic") == []


def test_curated_references_are_fetched_and_stored_as_job_evidence(tmp_path, monkeypatch):
    from app import db, learning, web_sources
    from app.db import learning_connection
    from app.web_sources import SourceDocument

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    fetched = []

    def fake_fetch(url, title, *, max_chars=6000):
        fetched.append(url)
        return SourceDocument(url=url, title=title, text=("SQL Server Database Engine query execution index transaction. " * 20))

    monkeypatch.setattr(web_sources, "discover_sources", lambda query, limit=3: [])
    monkeypatch.setattr(web_sources, "fetch_public_document", fake_fetch)
    job = learning.create_job("معماری SQL Server، Database Engine و ابزارهای مدیریت")
    text, source_kind = learning._discover_and_store_sources(job["id"], job["topic"])

    assert source_kind == "web_retrieved"
    assert len(fetched) == 2
    assert all(url in text for url in fetched)
    with learning_connection() as connection:
        evidence = connection.execute(
            "SELECT source_ref, content_hash FROM learning_evidence WHERE job_id=? AND evidence_type='web_page'",
            (job["id"],),
        ).fetchall()
    assert {row["source_ref"] for row in evidence} == set(fetched)
    assert all(row["content_hash"] for row in evidence)


def test_rejected_lesson_keeps_user_supplied_source_for_regeneration(tmp_path, monkeypatch):
    import json
    from app import db, learning
    from app.db import learning_connection

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    source_text = "User-provided reference material for the lesson. " * 30
    short_lesson = (
        "SQL Server و Database Engine درخواست T-SQL را پردازش می‌کنند. "
        "برای مثال، execution plan شیوه اجرای query را نشان می‌دهد. "
        "زیرا index و آمار بر انتخاب optimizer اثر می‌گذارند، logical reads را مقایسه کنید."
    )
    payload = {
        "lesson": short_lesson,
        "learning_outcomes": ["تشخیص اجزای موتور", "خواندن execution plan", "مقایسه logical reads"],
        "questions": ["Database Engine چه کاری انجام می‌دهد؟", "execution plan چه چیزی نشان می‌دهد؟", "چرا index همیشه سریع‌تر نیست؟"],
        "answer_key": ["درخواست‌ها را اجرا می‌کند.", "عملگرهای اجرای query را نشان می‌دهد.", "هزینه نگهداری و توزیع داده اثر دارند."],
    }
    monkeypatch.setattr(
        learning, "_request",
        lambda *args, **kwargs: {"message": {"content": json.dumps(payload, ensure_ascii=False)}},
    )
    job = learning.create_job("SQL Server", source_text=source_text)
    learning.run_job(job["id"])

    with learning_connection() as connection:
        stored = connection.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?", (job["id"],)
        ).fetchone()
        status = connection.execute(
            "SELECT status FROM learning_jobs WHERE id=?", (job["id"],)
        ).fetchone()["status"]
    assert stored is not None and stored["content"] == source_text.strip()
    assert status == "rejected"
