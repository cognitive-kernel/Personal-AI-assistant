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
