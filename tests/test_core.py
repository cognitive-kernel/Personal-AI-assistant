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


def test_startup_bootstrap_reads_project_dependencies():
    import run

    requirements = run._requirements(dev=False)
    assert any(item.startswith("fastapi") for item in requirements)
    assert any(item.startswith("uvicorn") for item in requirements)
    assert run.MIN_PYTHON == (3, 11)


def test_startup_bootstrap_detects_missing_distribution():
    import run

    assert run._installed("a-package-that-does-not-exist-anywhere-987654")
    is_missing = not run._installed("a-package-that-does-not-exist-anywhere-987654")
    assert is_missing
