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
