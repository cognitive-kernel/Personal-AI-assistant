import hashlib
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
    "نه تضمین خودکار برای درست بودن طراحی. "
    "برای ثبت نتیجه، نسخه SQL Server، تنظیمات مرتبط، حجم داده و شرایط اجرای آزمایش را مستند کنید. "
    "این اطلاعات امکان تکرار آزمایش را فراهم می‌کند و از مقایسه نتایج حاصل از محیط‌های متفاوت جلوگیری می‌کند. در یک بررسی عملی، همان پرس‌وجو را با پارامترهای ثابت چند بار اجرا کنید و میانهٔ زمان را کنار logical reads ثبت کنید. اگر نتایج متناقض‌اند، آمار قدیمی، تغییر هم‌زمان داده و فشار منابع را بررسی کنید و قبل از توصیهٔ قطعی آزمایش را تکرار کنید. "
    "منابع:\nhttps://docs.example.org/sql-guide\nhttps://learn.microsoft.com/sql-guide"
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
    db.update_settings({
        "cpu_resource_limit_percent": 55,
        "ram_budget_max_gb": 6,
        "ram_safety_reserve_gb": 5,
    })
    runtime_settings = {}
    monkeypatch.setattr(learning, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 24,
    })
    monkeypatch.setattr(
        learning,
        "runtime_limits",
        lambda settings, hardware: runtime_settings.update(settings) or {"num_ctx": 2048, "cpu_threads": 2},
    )
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
                "How should an index change be evaluated?",
            ],
            "answer_key": [
                "It shows the operations selected by the database engine.",
                "Indexes have maintenance costs and depend on data distribution.",
                "Compare baseline and changed reads on representative data.",
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
    assert runtime_settings["cpu_resource_limit_percent"] == 55
    assert runtime_settings["ram_budget_max_gb"] == 6
    assert runtime_settings["ram_safety_reserve_gb"] == 5
    assert finished["quality"]["accepted"] is True
    assert finished["quality"]["claim_review_status"] == (
        "passed_model_assisted" if passed else "needs_review"
    )
    assert finished["quality"]["evidence_gate"]["accepted"] is False
    assert finished["quality"]["evidence_gate_status"] == "needs_review"
    assert finished["quality"]["verification_status"] == "needs_review"
    assert "exact source URLs" in model_payloads[0]["messages"][-1]["content"]
    with db.learning_connection() as connection:
        knowledge = connection.execute(
            "SELECT status FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchone()
        event = connection.execute(
            "SELECT note FROM learning_evidence WHERE job_id=? AND evidence_type='claim_review'",
            (job["id"],),
        ).fetchone()
        persisted_quality = connection.execute(
            "SELECT quality_json FROM learning_jobs WHERE id=?", (job["id"],)
        ).fetchone()
    assert knowledge["status"] == "unverified"
    assert event is not None
    assert "not an independent verification" in event["note"]
    assert persisted_quality is not None
    assert json.loads(persisted_quality["quality_json"])["evidence_gate"]["accepted"] is False

def test_sql_server_claims_are_persisted_unverified_with_version_scope(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    url = "https://learn.microsoft.com/en-us/sql/sql-server/"
    excerpt = "SQL Server documentation explains query plans and database engine behavior. " * 4
    source_text = f"Source title: SQL Server docs\nSource URL: {url}\nContent:\n{excerpt}"
    job = learning.create_job("SQL Server 2022 execution plans")
    with db.learning_connection() as connection:
        digest = hashlib.sha256(excerpt.strip().encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'web_page', ?, ?, ?)""",
            (job["id"], url, digest, "Source content hash for regression test."),
        )
        stored = learning._persist_factual_review_claims(
            connection,
            job_id=job["id"],
            topic="SQL Server 2022 execution plans",
            source_text=source_text,
            review={
                "passed": True,
                "reviewer": "model_assisted_test",
                "claims": [{
                    "claim": "An execution plan describes selected query operations.",
                    "verdict": "supported",
                    "evidence_urls": [url],
                }],
            },
        )
        claim = connection.execute(
            """SELECT environment_name, environment_version, evidence_level, lifecycle_status
               FROM factual_knowledge WHERE claim = ?""",
            ("An execution plan describes selected query operations.",),
        ).fetchone()
        evidence = connection.execute(
            "SELECT source_type FROM factual_evidence WHERE source_ref=?",
            (url,),
        ).fetchone()
    assert stored == 1
    assert tuple(claim) == ("sql_server", "2022", "L0_raw", "unverified")
    assert evidence["source_type"] == "official_docs"


def test_factual_claim_persistence_skips_ambiguous_and_unrecognized_topics():
    assert learning._factual_domain_for_topic("General study skills") is None
    assert learning._factual_domain_for_topic("Python and SQL Server integration") is None


def test_official_documentation_host_matching_rejects_lookalikes():
    assert learning._source_type_for_factual_claim("https://learn.microsoft.com/en-us/sql/") == "official_docs"
    assert learning._source_type_for_factual_claim("https://learn.microsoft.com.attacker.example/sql/") == "other"

@pytest.mark.parametrize(
    ("topic", "environment_name", "environment_version"),
    [
        ("SQL Server 2022 execution plans", "sql_server", "2022"),
        ("Windows Server 2022 Active Directory", "windows_server", "2022"),
        ("MikroTik RouterOS 7.15 firewall", "mikrotik_routeros", "7.15"),
        ("VMware ESXi 8.0 storage", "vmware_esxi", "8.0"),
        ("Rust 1.82.0 ownership", "rust", "1.82.0"),
        ("Python 3.12 decorators", "python", "3.12"),
    ],
)
def test_factual_domain_detection_keeps_explicit_version_scope(topic, environment_name, environment_version):
    detected = learning._factual_domain_for_topic(topic)
    assert detected is not None
    assert detected[:2] == (environment_name, environment_version)
    assert detected[2] == "explicit_topic_version"



def test_claim_review_uses_configured_cpu_and_ram_limits(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    db.update_settings({
        "cpu_resource_limit_percent": 60,
        "ram_budget_max_gb": 7,
        "ram_safety_reserve_gb": 6,
    })
    runtime_settings = {}
    monkeypatch.setattr(claim_review, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "total_ram_gb": 32, "available_ram_gb": 20,
    })
    monkeypatch.setattr(
        claim_review,
        "runtime_limits",
        lambda settings, hardware: runtime_settings.update(settings) or {"num_ctx": 2048, "cpu_threads": 3},
    )
    monkeypatch.setattr(
        claim_review,
        "_request",
        lambda *_args, **_kwargs: {"message": {"content": "{}"}},
    )

    result = claim_review.review_answer_claims(
        "SQL Server question",
        "A factual answer with enough text to trigger claim review.",
        [{"url": "https://docs.example.org/guide", "content": "A source excerpt long enough to pass the source filter. " * 2}],
    )

    assert result["passed"] is False
    assert runtime_settings["cpu_resource_limit_percent"] == 60
    assert runtime_settings["ram_budget_max_gb"] == 7
    assert runtime_settings["ram_safety_reserve_gb"] == 6
