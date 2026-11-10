import json

import pytest

from app import db, learning, learning_curriculum_tasks, task_manager, web_sources
from app.task_worker import RetryableTaskError


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(learning, "_enqueue_learning_job", lambda job_id: {"id": 10000 + job_id})


class RecordingContext:
    def __init__(self, task_id=1, checkpoint_data=None):
        self.task_id = task_id
        self.checkpoint_data = checkpoint_data
        self.checkpoints = []

    def checkpoint(self, value):
        self.checkpoint_data = value
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}


def _outline():
    return {
        "title": "دوره جامع مبانی و مهندسی موضوع",
        "topics": [
            "تعریف‌ها و واژگان پایه",
            "پیش‌نیازها و مفاهیم بنیادی",
            "تاریخچه و سیر تحول",
            "معماری و اجزای اصلی",
            "اصول و سازوکارهای داخلی",
            "روش‌های پیاده‌سازی",
            "ابزارها و محیط‌های کاری",
            "امنیت و مدیریت ریسک",
            "عیب‌یابی و تحلیل خطا",
            "بهینه‌سازی و کارایی",
            "روش‌های پیشرفته",
            "پروژهٔ عملی و جمع‌بندی",
        ],
        "notes": "ترتیب از مبانی به مباحث پیشرفته است.",
        "coverage_areas": ["مبانی", "معماری", "عملی"],
    }


def _source(index):
    return web_sources.SourceDocument(
        url=f"https://docs{index}.example.test/guide",
        title=f"راهنمای مرجع {index}",
        text=(f"محتوای مستند و قابل‌استفاده برای موضوع شماره {index}. " * 30),
    )


def test_curriculum_payload_is_bounded_and_rejects_unknown_fields():
    assert learning_curriculum_tasks.validate_curriculum_payload({
        "topic": "Linux kernel",
        "max_topics": 40,
        "source_text": None,
    }) == ("Linux kernel", 40, "")
    with pytest.raises(ValueError):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "max_topics": 41,
            "source_text": None,
        })
    with pytest.raises(ValueError):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "max_topics": 40,
            "source_text": None,
            "unexpected": True,
        })
    with pytest.raises(ValueError, match="500"):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "max_topics": 40,
            "source_text": "x" * 501,
        })


def test_research_queries_preserve_each_search_angle_with_maximum_topic_length():
    topic = "x" * 300
    queries = learning_curriculum_tasks._research_queries(topic)
    assert len(queries) == len(learning_curriculum_tasks._SEARCH_QUERY_SUFFIXES)
    assert all(len(query) <= 300 for query in queries)
    assert all(
        query.endswith(suffix)
        for query, suffix in zip(queries, learning_curriculum_tasks._SEARCH_QUERY_SUFFIXES)
    )


def test_outline_parser_requires_unique_ordered_topics_and_complete_minimum():
    parsed = learning_curriculum_tasks._parse_outline(
        json.dumps(_outline(), ensure_ascii=False), 20
    )
    assert parsed["title"] == _outline()["title"]
    assert parsed["topics"] == _outline()["topics"]
    invalid = _outline()
    invalid["topics"][-1] = invalid["topics"][0]
    with pytest.raises(ValueError, match="duplicate"):
        learning_curriculum_tasks._parse_outline(json.dumps(invalid, ensure_ascii=False), 20)
    invalid = _outline()
    invalid["topics"] = invalid["topics"][:7]
    with pytest.raises(ValueError, match="between"):
        learning_curriculum_tasks._parse_outline(json.dumps(invalid, ensure_ascii=False), 20)


def test_research_task_persists_sources_outline_and_starts_only_first_lesson(
    learning_store, monkeypatch
):
    discovered = []
    def fake_discover(query, *, limit):
        discovered.append((query, limit))
        return [_source(len(discovered))]

    monkeypatch.setattr(web_sources, "discover_sources", fake_discover)
    model_calls = []
    monkeypatch.setattr(
        learning_curriculum_tasks.ollama,
        "chat",
        lambda message, **kwargs: (
            model_calls.append({"message": message, **kwargs})
            or json.dumps(_outline(), ensure_ascii=False)
        ),
    )
    task = task_manager.create_task(
        learning_curriculum_tasks.CURRICULUM_TASK_TYPE,
        {"topic": "موضوع فنی", "max_topics": 20, "source_text": ""},
        max_attempts=3,
    )
    context = RecordingContext(task["id"])

    result = learning_curriculum_tasks.handle_curriculum_task(
        task["payload"], None, context
    )

    assert len(discovered) == 4
    assert model_calls[0]["purpose"] == "learning"
    assert model_calls[0]["model_override"] == db.get_settings()["learning_ollama_model"]
    assert model_calls[0]["num_predict"] == 800
    assert result["source_count"] == 4
    assert result["queued_count"] == 1
    assert len(result["queued_job_ids"]) == 1
    path = learning.get_learning_path(result["path_id"])
    assert path["research"]["requested_topic"] == "موضوع فنی"
    assert path["research"]["model"] == db.get_settings()["learning_ollama_model"]
    assert path["research"]["model_selection"]["selected_model"] == db.get_settings()["learning_ollama_model"]
    assert path["research"]["outline_sha256"] == result["outline_sha256"]
    assert len(path["research"]["sources"]) == 4
    assert len(path["topics"]) == len(_outline()["topics"])
    assert path["topics"][0]["status"] == "in_progress"
    assert all(item["status"] == "pending" for item in path["topics"][1:])
    assert context.checkpoint_data["phase"] == "curriculum_persisted"

    retry_context = RecordingContext(task["id"], context.checkpoint_data)
    retried = learning_curriculum_tasks.handle_curriculum_task(
        task["payload"], context.checkpoint_data, retry_context
    )
    assert retried["path_id"] == result["path_id"]
    assert retried["outline_sha256"] == result["outline_sha256"]
    assert len(discovered) == 4
    assert len(model_calls) == 1
    assert retried["queue_status"] == "already_active"


def test_researched_path_queues_next_lesson_only_after_previous_job_succeeds(
    learning_store, monkeypatch
):
    sources = [_source(1), _source(2), _source(3)]
    path_id, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=901,
        topic="موضوع ترتیبی",
        outline=_outline(),
        sources=[
            {
                "title": item.title,
                "url": item.url,
                "excerpt": item.text[:900],
                "sha256": __import__("hashlib").sha256(item.text.encode()).hexdigest(),
            }
            for item in sources
        ],
        model="gpt-oss:20b",
        model_selection={"status": "configured_fallback", "selected_model": "gpt-oss:20b"},
    )

    first = learning.start_researched_path_learning(path_id)
    assert first["queued_count"] == 1
    path = learning.get_learning_path(path_id)
    first_topic_id = path["topics"][0]["id"]
    first_job_id = first["job_ids"][0]

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed' WHERE id=?",
            (first_job_id,),
        )
    second = learning.queue_next_researched_path_topic(first_topic_id)
    assert second is not None and second["queued_count"] == 1
    assert second["job_ids"][0] != first_job_id

    with db.learning_connection() as connection:
        jobs = connection.execute(
            "SELECT path_topic_id, status FROM learning_jobs ORDER BY id"
        ).fetchall()
    assert len(jobs) == 2
    assert jobs[0]["status"] == "completed"
    assert jobs[1]["status"] == "queued"
    assert jobs[1]["path_topic_id"] == path["topics"][1]["id"]


def test_researched_path_does_not_advance_after_failed_lesson(learning_store, monkeypatch):
    path_id, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=902,
        topic="موضوع ناموفق",
        outline=_outline(),
        sources=[
            {
                "title": item.title,
                "url": item.url,
                "excerpt": item.text[:900],
                "sha256": __import__("hashlib").sha256(item.text.encode()).hexdigest(),
            }
            for item in [_source(1), _source(2), _source(3)]
        ],
        model="gpt-oss:20b",
        model_selection={"status": "configured_fallback", "selected_model": "gpt-oss:20b"},
    )
    first = learning.start_researched_path_learning(path_id)
    first_topic_id = learning.get_learning_path(path_id)["topics"][0]["id"]
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='failed' WHERE id=?",
            (first["job_ids"][0],),
        )
    blocked = learning.queue_next_researched_path_topic(first_topic_id)
    assert blocked["status"] == "blocked_by_previous_lesson"
    assert blocked["queued_count"] == 0
    with db.learning_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM learning_jobs").fetchone()[0] == 1
    resumed = learning.start_path_learning(path_id)
    assert resumed["queued_count"] == 1
    assert resumed["job_ids"][0] != first["job_ids"][0]


def test_research_requires_multiple_usable_web_domains(learning_store, monkeypatch):
    monkeypatch.setattr(web_sources, "discover_sources", lambda *_args, **_kwargs: [_source(1)])
    context = RecordingContext()
    with pytest.raises(RetryableTaskError, match="three usable pages"):
        learning_curriculum_tasks._collect_sources(
            "موضوع کم‌منبع",
            topic_sha256="a" * 64,
            checkpoint=None,
            context=context,
        )
    assert context.checkpoint_data["phase"] == "web_research_retry"


def test_ollama_chat_applies_curriculum_output_token_limit(monkeypatch):
    from app import ollama, usage_metrics

    captured = {}
    monkeypatch.setattr(ollama, "get_settings", lambda: {
        "learning_ollama_timeout_seconds": 300,
        "learning_ollama_num_ctx": 2048,
        "learning_ollama_num_thread": 1,
        "cpu_resource_limit_percent": 90,
        "ram_budget_max_gb": 25,
        "ram_safety_reserve_gb": 4,
        "gpu_enabled": False,
    })
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {"logical_cpu_count": 12})
    monkeypatch.setattr(ollama, "runtime_limits", lambda *_args, **_kwargs: {
        "num_ctx": 2048, "cpu_threads": 1,
    })
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *_args, **_kwargs: "5m")

    def fake_request(path, payload=None, **_kwargs):
        if path == "/api/show":
            return {
                "model_info": {"llama.context_length": 2048},
                "details": {"family": "llama"},
            }
        captured.update(payload)
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(ollama, "_request", fake_request)
    monkeypatch.setattr(usage_metrics, "record_ollama_response", lambda **_kwargs: None)
    result = ollama.chat(
        "Build a course outline",
        purpose="learning",
        model_override="gpt-oss:20b",
        num_predict=800,
    )
    assert result == "{}"
    assert captured["options"]["num_predict"] == 800
    assert captured["options"]["num_gpu"] == 0
    assert captured["model"] == "gpt-oss:20b"


def test_dashboard_uses_researched_courses_and_hides_preset_catalog_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    script = (root / "app" / "static" / "learning.js").read_text(encoding="utf-8")
    assert 'id="learning-form"' in page
    assert "/api/learning/courses" in page
    assert 'id="curriculum-select"' not in page
    assert 'id="enroll-curriculum"' not in page
    assert "loadLearningCurricula();" not in script
