import json

import pytest

from app import claim_review, db, learning


LESSON = (
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


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_claim_review_source_parser_extracts_only_bounded_https_sources():
    url = "https://docs.example.org/sql-guide"
    text = (
        f"Source title: SQL Guide\nSource URL: {url}\nContent:\n"
        + ("This excerpt documents a database execution plan and query optimizer. " * 10)
    )
    sources = learning._claim_review_sources(text)
    assert sources == [{
        "title": "SQL Guide",
        "url": url,
        "content": ("This excerpt documents a database execution plan and query optimizer. " * 10).strip()[:2500],
    }]
    assert learning._claim_review_sources("Source URL: http://localhost/private\nContent:\n" + "x" * 200) == []


@pytest.mark.parametrize("passed", [True, False])
def test_learning_job_records_claim_review_without_promoting_knowledge_to_verified(
    tmp_path, monkeypatch, passed
):
    _setup(tmp_path, monkeypatch)
    url = "https://docs.example.org/sql-guide"
    source_text = (
        f"Source title: SQL Guide\nSource URL: {url}\nContent:\n"
        + ("This excerpt documents a database execution plan and query optimizer. " * 10)
    )
    job = learning.create_job("SQL Server execution plans")
    with db.learning_connection() as connection:
        connection.execute(
            "INSERT INTO learning_source_text(job_id, content) VALUES (?, ?)",
            (job["id"], source_text),
        )
        connection.execute(
            "UPDATE learning_jobs SET source_kind='web_retrieved' WHERE id=?",
            (job["id"],),
        )
        connection.execute(
            """INSERT INTO learning_evidence(job_id, evidence_type, source_ref, content_hash, note)
               VALUES (?, 'web_page', ?, NULL, ?)""",
            (job["id"], url, "Source has not been independently verified."),
        )

    model_payloads = []

    def fake_model(_path, payload, **_kwargs):
        model_payloads.append(payload)
        return {"message": {"content": json.dumps({
            "lesson": LESSON,
            "learning_outcomes": ["Explain query plans", "Compare index costs", "Measure logical reads"],
            "questions": [
                "What does an execution plan show?",
                "Why can an index increase costs?",
                "How would you evaluate an index change in a new workload scenario?",
            ],
            "answer_key": [
                "It shows the operations selected by the database engine.",
                "Indexes have maintenance costs and depend on data distribution.",
                "Compare baseline and changed reads on representative data.",
            ],
            "answer_rubric": [
                {"required_concepts": [
                    {"concept": "execution plan", "accepted_phrases": ["execution plan", "query plan"]},
                    {"concept": "execution operations", "accepted_phrases": ["operations", "operators", "execution steps"]},
                ]},
                {"required_concepts": [
                    {"concept": "index maintenance costs", "accepted_phrases": ["maintenance costs", "cost of maintaining indexes"]},
                    {"concept": "data distribution", "accepted_phrases": ["data distribution", "distribution of data"]},
                ]},
                {"required_concepts": [
                    {"concept": "baseline comparison", "accepted_phrases": ["baseline", "before and after"]},
                    {"concept": "logical reads", "accepted_phrases": ["logical reads", "read counts"]},
                ]},
            ],
        }, ensure_ascii=False)}}

    monkeypatch.setattr(learning, "_request", fake_model)
    review = {
        "passed": passed,
        "overall": "supported" if passed else "insufficient_evidence",
        "claims": [{
            "claim": "The lesson's technical claims were reviewed.",
            "verdict": "supported" if passed else "insufficient_evidence",
            "evidence_urls": [url] if passed else [],
            "reason": "Test review result.",
        }],
        "error": None if passed else "review needs review",
        "reviewer": "local_ollama_model_assisted_not_independent",
    }
    monkeypatch.setattr(claim_review, "review_answer_claims", lambda question, answer, sources: (
        review if sources[0]["url"] == url and answer == LESSON else {"passed": False}
    ))

    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    assert finished["status"] == "completed"
    assert finished["quality"]["accepted"] is True
    # A failed provenance gate must be visible, but should not make an otherwise
    # usable lesson disappear; scientific acceptance remains a separate status.
    assert finished["quality"]["scientific_acceptance"]["accepted"] is False
    assert finished["quality"]["scientific_acceptance"]["lesson_available_for_learning"] is True
    assert finished["quality"]["scientific_acceptance"]["knowledge_verification_status"] == "unverified"
    assert finished["quality"]["claim_review_status"] == (
        "passed_model_assisted" if passed else "needs_review"
    )
    assert "exact source URLs" in model_payloads[0]["messages"][-1]["content"]
    with db.learning_connection() as connection:
        knowledge = connection.execute(
            "SELECT status FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchone()
        event = connection.execute(
            "SELECT note FROM learning_evidence WHERE job_id=? AND evidence_type='claim_review'",
            (job["id"],),
        ).fetchone()
    assert knowledge["status"] == "unverified"
    assert event is not None
    assert "not an independent verification" in event["note"]
