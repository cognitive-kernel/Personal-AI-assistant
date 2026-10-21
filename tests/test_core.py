from app import db, hardware, learning, ollama

def test_databases_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.settings_connection() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with db.learning_connection() as conn:
        learning_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "settings" in tables and "learning_jobs" not in tables
    assert "learning_jobs" in learning_tables and "settings" not in learning_tables

def test_runtime_limits_respect_cpu_and_memory():
    limits = hardware.runtime_limits({"ollama_num_thread": 20, "ollama_num_ctx": 8192},
                                     {"logical_cpu_count": 6, "total_ram_gb": 8})
    assert limits == {"cpu_threads": 6, "num_ctx": 2048}

def test_settings_reject_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    try:
        db.update_settings({"arbitrary_key": "value"})
    except ValueError as exc:
        assert "Unsupported settings" in str(exc)
    else:
        raise AssertionError("Unknown setting should be rejected")

def test_learning_job_records_source_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Python decorators", "A decorator wraps a function.")
    assert job["status"] == "queued"
    assert job["source_kind"] == "user_provided_text"
    assert job["evidence"][0]["content_hash"]
    assert job["evidence"][0]["evidence_type"] == "user_text"

def test_knowledge_is_not_automatically_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("HTTP fundamentals")
    with db.learning_connection() as conn:
        conn.execute(
            "INSERT INTO knowledge_items(topic, content, source_kind, version, job_id) VALUES (?, ?, ?, ?, ?)",
            ("HTTP fundamentals", "Generated note", job["source_kind"], 1, job["id"]),
        )
        row = conn.execute("SELECT status FROM knowledge_items WHERE job_id=?", (job["id"],)).fetchone()
    assert row["status"] == "unverified"

def test_learning_source_limit_is_configurable_and_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    try:
        learning.create_job("Long source", "x" * 12001)
    except ValueError as exc:
        assert "current limit: 12000" in str(exc)
    else:
        raise AssertionError("Oversized source should be rejected")


def test_ollama_rejects_non_loopback_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    db.update_settings({"ollama_base_url": "http://example.com:11434"})
    try:
        ollama._base_url()
    except ollama.OllamaError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Remote Ollama endpoints should be rejected")


def test_learning_quality_gate_rejects_source_failure_reports():
    lesson = """VERSION TARGET: 2026-current. Source Unavailability and Network Restrictions. A client error 403 Forbidden prevented fetching the requested source. Local/private/reserved network targets are blocked. Recommendations for Future Actions: use alternative sources, retry later, and check network configuration. The source could not be reached and no actual lesson content was extracted. These notes describe the failed retrieval attempt, rather than explaining the requested topic. The same issue was reported for multiple URLs and the output repeats generic advice without teaching the target concepts."""
    result = learning.evaluate_learning_value(lesson, ["What failed?", "Which source?", "What next?"], ["Retrieval failed.", "A web source.", "Try another source."])
    assert result["accepted"] is False
    assert result["score"] < 65
    assert "محتوا بیشتر گزارش خطا/دسترسی منبع است تا آموزش موضوعی." in result["reasons"]


def test_learning_quality_gate_accepts_substantive_lesson():
    lesson = ("Python decorators let a program wrap a function with reusable behavior. A decorator receives a callable and returns another callable, often preserving metadata with functools.wraps. The @ syntax applies the decorator when the function is defined. Decorators are useful for logging, caching, authorization, and measuring execution time. A simple decorator defines an inner wrapper, calls the original function, and returns its result. Arguments can be forwarded with *args and **kwargs. Decorator order matters because decorators are applied from the nearest function outward. For methods, the wrapper must preserve descriptor behavior expected by the class. Use a decorator when behavior should be shared across functions; avoid it when it hides important control flow.")
    result = learning.evaluate_learning_value(lesson, ["What does a decorator receive?", "Why use wraps?", "When are decorators useful?"], ["A callable.", "To preserve metadata.", "For reusable cross-cutting behavior."])
    assert result["accepted"] is True
    assert result["score"] >= 65


def _load_startup_bootstrap():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "run.py"
    spec = importlib.util.spec_from_file_location("startup_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_startup_bootstrap_reads_project_dependencies():
    bootstrap = _load_startup_bootstrap()

    requirements = bootstrap._requirements(dev=False)
    assert any(item.startswith("fastapi") for item in requirements)
    assert any(item.startswith("uvicorn") for item in requirements)
    assert bootstrap.MIN_PYTHON == (3, 11)


def test_startup_bootstrap_detects_missing_distribution():
    bootstrap = _load_startup_bootstrap()

    assert not bootstrap._installed("a-package-that-does-not-exist-anywhere-987654")


def test_learning_curriculum_catalog_includes_requested_system_tracks():
    from app import curricula

    listed = {item["slug"] for item in curricula.list_curricula()}
    assert {"python", "sql-server", "forex-metatrader", "mcse-windows-server",
            "mikrotik-routeros", "vmware-esxi"} <= listed


def test_learning_curriculum_aliases_and_topics():
    from app.curricula import get_curriculum

    assert get_curriculum("MCSE")["slug"] == "mcse-windows-server"
    assert get_curriculum("MikroTik")["slug"] == "mikrotik-routeros"
    esx = get_curriculum("ESX")
    assert esx["slug"] == "vmware-esxi"
    assert any("vMotion" in topic for topic in esx["topics"])
    for course in (get_curriculum("Python"), get_curriculum("MCSE"),
                   get_curriculum("MikroTik"), esx):
        assert [level["level"] for level in course["levels"]] == ["beginner", "intermediate", "advanced"]
        assert [topic for level in course["levels"] for topic in level["topics"]] == course["topics"]
    assert "آشنایی مقدماتی با رایانه و سخت‌افزار" in get_curriculum("MCSE")["prerequisites"]
    assert get_curriculum("not-a-real-course") is None


def test_prerequisite_cache_pins_existing_wheels(tmp_path, monkeypatch):
    bootstrap = _load_startup_bootstrap()
    monkeypatch.setattr(bootstrap, "WHEELHOUSE", tmp_path / "wheels")
    bootstrap.WHEELHOUSE.mkdir()
    (bootstrap.WHEELHOUSE / "fastapi-0.115.0-py3-none-any.whl").write_bytes(b"cached")
    (bootstrap.WHEELHOUSE / "pydantic_core-2.20.0-cp311-cp311-manylinux.whl").write_bytes(b"cached")
    pins = bootstrap._cached_package_pins()
    assert pins["fastapi"] == "0.115.0"
    assert pins["pydantic-core"] == "2.20.0"


def test_prerequisite_manifest_skips_download_when_cache_is_complete(tmp_path, monkeypatch):
    import json

    bootstrap = _load_startup_bootstrap()
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    (wheelhouse / "cached.whl").write_bytes(b"cached")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"fingerprint": "abc", "files": ["cached.whl"]}), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "WHEELHOUSE", wheelhouse)
    monkeypatch.setattr(bootstrap, "CACHE_MANIFEST", manifest)
    assert bootstrap._cache_is_complete("abc")
    assert not bootstrap._cache_is_complete("changed")

def test_expanded_curriculum_catalog_contains_advanced_learning_paths():
    from app import curricula

    ai = curricula.get_curriculum("AI")
    forex = curricula.get_curriculum("Forex")
    python = curricula.get_curriculum("Python")
    assert len(ai["topics"]) >= 120
    assert len(forex["topics"]) >= 115
    assert len(python["topics"]) >= 35
    assert any("MQL5" in topic for topic in forex["topics"])
    assert any("Expert Python capstone" in topic for topic in python["topics"])


def test_partial_prerequisite_cache_downloads_only_missing_distribution(tmp_path, monkeypatch):
    import subprocess

    bootstrap = _load_startup_bootstrap()
    monkeypatch.setattr(bootstrap, "WHEELHOUSE", tmp_path / "wheels")
    bootstrap.WHEELHOUSE.mkdir()
    (bootstrap.WHEELHOUSE / "existing-1.0-py3-none-any.whl").write_bytes(b"cached")
    monkeypatch.setattr(bootstrap, "_cache_is_complete", lambda _fingerprint: False)
    monkeypatch.setattr(bootstrap, "_cached_package_pins", lambda: {"existing": "1.0"})
    probes = [
        subprocess.CompletedProcess([], 1, "", "ERROR: No matching distribution found for missing-dep>=2"),
        subprocess.CompletedProcess([], 0, "", ""),
    ]
    monkeypatch.setattr(bootstrap, "_probe_local_wheelhouse", lambda _requirements: probes.pop(0))
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    monkeypatch.setattr(bootstrap, "_write_cache_manifest", lambda _fingerprint: None)
    bootstrap._ensure_wheelhouse(["demo>=1"], "fingerprint")
    assert len(commands) == 1
    assert "--no-deps" in commands[0]
    assert commands[0][-1] == "missing-dep>=2"


def test_assessment_answers_are_persisted_and_answer_key_is_hidden_until_submission(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Python decorators")
    with db.learning_connection() as conn:
        conn.execute(
            """UPDATE learning_jobs SET status='completed', lesson=?, assessment_json=?
               WHERE id=?""",
            ("Lesson text", json.dumps({
                "questions": ["What does a decorator receive?", "Why use wraps?", "When is it useful?"],
                "answer_key": ["A callable", "To preserve metadata", "Reusable behavior"],
                "independently_verified": False,
            }), job["id"]),
        )
    before = learning.get_job(job["id"])
    assert before["assessment"]["questions"]
    assert "answer_key" not in before["assessment"]
    assert "answer_rubric" not in before["assessment"]
    result = learning.submit_assessment(job["id"], ["A callable", "wrong", "Reusable behavior"])
    assert result["correct_count"] == 2
    assert result["score"] == 66.67
    assert result["independent_verification"] is False
    after = learning.get_job(job["id"])
    assert after["assessment"]["answer_key"][0] == "A callable"
    assert after["assessment_attempts"][0]["score"] == 66.67


def test_assessment_rejects_wrong_answer_count(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("HTTP")
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps({"questions": ["Q1", "Q2"], "answer_key": ["A1", "A2"]}), job["id"]),
        )
    try:
        learning.submit_assessment(job["id"], ["A1"])
    except ValueError as exc:
        assert "count must match" in str(exc)
    else:
        raise AssertionError("Wrong answer count should be rejected")


def test_learning_path_enrollment_and_progress_are_persistent(tmp_path, monkeypatch):
    import json
    import pytest

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    path = learning.enroll_curriculum("Python")
    assert path["topic_count"] > 0
    assert path["completed_count"] == 0
    assert path["progress_percent"] == 0
    first_topic = path["topics"][0]

    with pytest.raises(ValueError, match="requires a latest rubric-based assessment"):
        learning.update_path_topic(path["id"], first_topic["id"], "completed", 100)

    job = learning.create_job("Python", path_topic_id=first_topic["id"])
    rubric = [
        {"required_concepts": [
            {"concept": "alpha", "accepted_phrases": ["alpha"]},
            {"concept": "beta", "accepted_phrases": ["beta"]},
        ]}
        for _ in range(3)
    ]
    assessment = {
        "questions": ["Q1", "Q2", "Q3"],
        "answer_key": ["alpha beta", "alpha beta", "alpha beta"],
        "answer_rubric": rubric,
    }
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', assessment_json=? WHERE id=?",
            (json.dumps(assessment), job["id"]),
        )
    result = learning.submit_assessment(job["id"], ["alpha beta", "alpha beta", "alpha beta"])
    assert result["scoring_method"] == "rubric_concept_coverage_v1"
    assert result["score"] == 100.0

    updated = learning.get_learning_path(path["id"])
    assert updated["completed_count"] == 1
    assert updated["progress_percent"] > 0
    again = learning.enroll_curriculum("Python")
    assert again["id"] == path["id"]
    assert again["completed_count"] == 1
    assert len(again["topics"]) == path["topic_count"]


def test_learning_path_rejects_invalid_status_and_unknown_curriculum(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    path = learning.enroll_curriculum("MCSE")
    try:
        learning.update_path_topic(path["id"], path["topics"][0]["id"], "skipped")
    except ValueError as exc:
        assert "Status must be" in str(exc)
    else:
        raise AssertionError("Invalid progress status should be rejected")
    try:
        learning.enroll_curriculum("unknown-course")
    except KeyError:
        pass
    else:
        raise AssertionError("Unknown curriculum should be rejected")



def test_sync_path_preserves_progress_and_job_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    path = learning.enroll_curriculum("sql-server")
    with db.learning_connection() as conn:
        next_position = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM learning_path_topics WHERE path_id=?",
            (path["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path["id"], "Legacy unstarted duplicate", next_position),
        )
        conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position, status) VALUES (?, ?, ?, 'completed')",
            (path["id"], "Legacy completed topic", next_position + 1),
        )
        legacy_job_id = conn.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
            (path["id"], "Legacy topic with job", next_position + 2),
        ).lastrowid
    job = learning.create_job("Legacy topic with job", path_topic_id=legacy_job_id)
    result = learning.sync_curriculum_path(path["id"])
    topics = {item["topic"]: item for item in result["topics"]}
    assert "Legacy unstarted duplicate" not in topics
    assert topics["Legacy completed topic"]["status"] == "completed"
    assert "Legacy topic with job" in topics
    assert learning.get_job(job["id"])["path_topic_id"] == legacy_job_id
    assert result["sync"]["removed_legacy_count"] == 1
    assert result["sync"]["retained_legacy_count"] == 2
    assert result["sync"]["progress_preserved"] is True
    assert len(result["topics"]) == 21


def test_lesson_generation_sets_explicit_output_budget(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
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
    payloads = []
    def fake_request(_path, payload, **_kwargs):
        payloads.append(payload)
        return {"message": {"content": json.dumps({
            "lesson": lesson,
            "learning_outcomes": ["توضیح مسیر اجرای پرس‌وجو", "مقایسه اثر ایندکس", "اندازه‌گیری logical reads"],
            "questions": [
                "execution plan چه اطلاعاتی درباره اجرای پرس‌وجو می‌دهد؟",
                "چرا ایندکس همیشه باعث بهبود عملکرد نمی‌شود؟",
                "اگر بار کاری تغییر کند، چطور اثر یک ایندکس آزمایشی را بررسی می‌کنیم؟",
            ],
            "answer_key": [
                "نشان می‌دهد موتور چگونه داده را می‌خواند و چه عملگرهایی را اجرا می‌کند.",
                "زیرا هزینه نگهداری، توزیع داده و انتخاب بهینه‌ساز بر نتیجه اثر می‌گذارند.",
                "با ثبت خط مبنا و مقایسه logical reads و زمان اجرا روی داده نماینده.",
            ],
            "answer_rubric": [
                {"required_concepts": [
                    {"concept": "execution plan", "accepted_phrases": ["execution plan", "query execution plan"]},
                    {"concept": "operations", "accepted_phrases": ["operations", "operators", "execution steps"]},
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
    def fake_llm_complete(_task, messages, options=None, response_format=None, **_kwargs):
        payloads.append({"format": response_format, "messages": messages, "options": options})
        return fake_request("/api/chat", {"format": response_format, "messages": messages, "options": options})

    payloads.clear()
    monkeypatch.setattr(learning, "llm_complete", fake_llm_complete)
    job = learning.create_job("معماری SQL Server و ابزارهای مدیریت")
    learning.run_job(job["id"])
    finished = learning.get_job(job["id"])
    assert payloads[0]["options"]["num_predict"] >= 1000
    assert finished["status"] == "completed"
    assert finished["quality"]["accepted"] is True
    assert finished["quality"]["scientific_acceptance"]["accepted"] is False
    assert finished["quality"]["scientific_acceptance"]["lesson_available_for_learning"] is True
    assert finished["quality"]["scientific_acceptance"]["knowledge_verification_status"] == "unverified"


def test_learning_job_failure_is_recorded_and_propagated(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Failure propagation test")

    def fail_request(*_args, **_kwargs):
        raise RuntimeError("model offline")

    monkeypatch.setattr(learning, "llm_complete", fail_request)
    try:
        learning.run_job(job["id"])
    except learning.LearningError as exc:
        assert "model offline" in str(exc)
    else:
        raise AssertionError("Learning execution failure must propagate to the task worker")

    failed = learning.get_job(job["id"])
    assert failed["status"] == "failed"
    assert "model offline" in failed["error"]


def test_learning_job_failure_is_recorded_and_propagated(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Failure propagation test")

    def fail_request(*_args, **_kwargs):
        raise RuntimeError("model offline")

    monkeypatch.setattr(learning, "llm_complete", fail_request)
    try:
        learning.run_job(job["id"])
    except learning.LearningError as exc:
        assert "model offline" in str(exc)
    else:
        raise AssertionError("Learning execution failure must propagate to the task worker")

    failed = learning.get_job(job["id"])
    assert failed["status"] == "failed"
    assert "model offline" in failed["error"]


def test_learning_job_records_explicit_https_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    url = "https://docs.python.org/3/tutorial/"
    job = learning.create_job("Python tutorial", source_url=url)
    assert job["status"] == "queued"
    assert any(
        item["evidence_type"] == "requested_url" and item["source_ref"] == url
        for item in job["evidence"]
    )


def test_learning_job_rejects_non_https_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    try:
        learning.create_job("Unsafe source", source_url="http://example.org/guide")
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Non-HTTPS source URL should be rejected")


def test_failed_explicit_source_url_does_not_trigger_automatic_substitution(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Requested source must not be silently replaced", source_url="https://unreachable.example/lesson")

    def fail_fetch(*_args, **_kwargs):
        raise OSError("requested URL unavailable")

    research_calls = []
    monkeypatch.setattr("app.web_sources.fetch_public_document", fail_fetch)
    monkeypatch.setattr(
        "app.knowledge_review.research_candidate_online",
        lambda *args, **kwargs: research_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("app.knowledge_review.consult_configured_peer_ais", lambda *args, **kwargs: {
        "status": "unconfigured", "consulted": 0, "errors": [],
    })
    monkeypatch.setattr("app.learning_verification.run_candidate_source_doctests", lambda *_args, **_kwargs: {
        "status": "no_examples", "promoted": False,
    })
    monkeypatch.setattr(learning, "llm_complete", lambda *_args, **_kwargs: {
        "message": {"content": json.dumps({
            "lesson": "A concise candidate lesson.",
            "learning_outcomes": ["Outcome one", "Outcome two", "Outcome three"],
            "questions": ["Recall?", "Why?", "Apply to a new case?"],
            "answer_key": ["One", "Two", "Three"],
            "answer_rubric": [],
        })}
    })

    learning.run_job(job["id"])

    assert research_calls == []
    with db.learning_connection() as conn:
        candidate = conn.execute(
            "SELECT id, status FROM temporary_knowledge_candidates WHERE job_id=?", (job["id"],)
        ).fetchone()
        attempt = conn.execute(
            "SELECT status, details_json FROM knowledge_review_attempts "
            "WHERE candidate_id=? AND channel='web_research' ORDER BY id DESC LIMIT 1",
            (candidate["id"],),
        ).fetchone()
    assert candidate["status"] == "awaiting_test"
    assert attempt["status"] == "unavailable"
    assert "explicit_requested_url_failed_no_automatic_substitution" in attempt["details_json"]


def test_explicit_source_url_success_is_used_and_recorded(tmp_path, monkeypatch):
    import json
    from app.web_sources import SourceDocument

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    url = "https://docs.example.org/python"
    job = learning.create_job("Python source ingestion", source_url=url)
    monkeypatch.setenv("LEARNING_AUTO_WEB_RESEARCH", "true")
    source_text = "Python documentation explains functions, parameters, and return values with examples."
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *_args, **_kwargs: SourceDocument(url=url, title="Python reference", text=source_text),
    )
    monkeypatch.setattr("app.claim_review.review_answer_claims", lambda *_args, **_kwargs: {
        "passed": False, "overall": "insufficient_evidence", "claims": [], "source_conflicts": [],
    })
    research_calls = []
    monkeypatch.setattr(
        "app.knowledge_review.research_candidate_online",
        lambda *args, **kwargs: research_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("app.knowledge_review.consult_configured_peer_ais", lambda *args, **kwargs: {
        "status": "unconfigured", "consulted": 0, "errors": [],
    })
    monkeypatch.setattr("app.learning_verification.run_candidate_source_doctests", lambda *_args, **_kwargs: {
        "status": "no_examples", "promoted": False,
    })
    payloads = []
    monkeypatch.setattr(learning, "llm_complete", lambda _task, messages, options=None, response_format=None, **_kwargs: (
        payloads.append({"messages": messages, "options": options, "format": response_format}) or {"message": {"content": json.dumps({
            "lesson": "A concise lesson about Python functions.",
            "learning_outcomes": ["Outcome one", "Outcome two", "Outcome three"],
            "questions": ["Recall?", "Why?", "Apply to a new case?"],
            "answer_key": ["One", "Two", "Three"],
            "answer_rubric": [],
        })}}
    ))

    learning.run_job(job["id"])

    assert source_text in payloads[0]["messages"][1]["content"]
    assert research_calls
    with db.learning_connection() as conn:
        evidence = conn.execute(
            "SELECT source_ref, note FROM learning_evidence WHERE job_id=? AND evidence_type='web_page'",
            (job["id"],),
        ).fetchone()
        candidate = conn.execute(
            "SELECT source_urls_json, status FROM temporary_knowledge_candidates WHERE job_id=?",
            (job["id"],),
        ).fetchone()
    assert evidence["source_ref"] == url
    assert "Factual claims still require validation" in evidence["note"]
    assert url in json.loads(candidate["source_urls_json"])
    assert candidate["status"] == "awaiting_test"
