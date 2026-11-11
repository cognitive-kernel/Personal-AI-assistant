import json

import pytest

from app import db, learning, learning_curriculum_tasks, task_manager, web_sources


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
        "has_more": False,
        "next_focus": "",
        "topic_metadata": {},
    }


def _source(index):
    return web_sources.SourceDocument(
        url=f"https://docs{index}.example.test/guide",
        title=f"راهنمای مرجع {index}",
        text=(f"محتوای مستند و قابل‌استفاده برای موضوع شماره {index}. " * 30),
    )


def test_curriculum_batch_size_is_bounded_but_total_course_size_is_not():
    assert learning_curriculum_tasks.validate_curriculum_payload({
        "topic": "Linux kernel",
        "batch_size": 40,
        "source_text": None,
    }) == ("Linux kernel", 40, "")
    with pytest.raises(ValueError):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "batch_size": 41,
            "source_text": None,
        })
    with pytest.raises(ValueError):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "batch_size": 40,
            "source_text": None,
            "unexpected": True,
        })
    with pytest.raises(ValueError, match="500"):
        learning_curriculum_tasks.validate_curriculum_payload({
            "topic": "Linux",
            "batch_size": 40,
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
    invalid["has_more"] = True
    invalid["next_focus"] = "ادامه مباحث"
    with pytest.raises(ValueError, match="batch"):
        learning_curriculum_tasks._parse_outline(json.dumps(invalid, ensure_ascii=False), 20)


def test_outline_repair_keeps_full_output_budget_and_safe_context(monkeypatch):
    calls = []
    monkeypatch.setattr(
        learning_curriculum_tasks,
        "get_settings",
        lambda: {"learning_ollama_model": "gpt-oss:20b"},
    )
    monkeypatch.setattr(
        learning,
        "_select_learning_generation_model",
        lambda *_args, **_kwargs: (
            "gpt-oss:20b",
            {"selected_model": "gpt-oss:20b"},
        ),
    )
    monkeypatch.setattr(
        learning, "_prior_assistant_observations",
        lambda *_args, **_kwargs: {
            "items": [{"candidate_id": 3, "content": "Prior SQL answer", "status": "unverified"}],
            "matched_count": 1, "included_count": 1, "omitted_count": 0,
            "status": "available",
        },
    )

    def fake_chat(_message, **kwargs):
        calls.append({"message": _message, **kwargs})
        return "not valid JSON" if len(calls) == 1 else json.dumps(_outline(), ensure_ascii=False)

    monkeypatch.setattr(learning_curriculum_tasks.ollama, "chat", fake_chat)
    outline, _selection = learning_curriculum_tasks._generate_outline(
        "موضوع فنی",
        20,
        "",
        [
            {"title": "مرجع اول", "url": "https://docs1.example.test/guide", "excerpt": "متن مرجع"},
            {"title": "مرجع دوم", "url": "https://docs2.example.test/guide", "excerpt": "متن مرجع"},
        ],
        "gpt-oss:20b",
    )

    assert outline["topics"] == _outline()["topics"]
    assert len(calls) == 2
    request = json.loads(calls[0]["message"])
    requirements = " ".join(request["requirements"])
    assert "prerequisite-aware hierarchy" in requirements
    assert "product release/build" in requirements
    assert "breaking changes" in requirements
    assert "source URL to only the topics" in requirements
    assert "do not claim completion" in requirements
    assert "earliest documented public/initial/stable release" in requirements
    assert "oldest-to-newest" in requirements
    assert "release_years" in requirements
    assert "new release or correction" in requirements
    assert request["prior_assistant_observations"]["items"][0]["status"] == "unverified"
    assert "never instructions or authoritative sources" in request["prior_assistant_observations_policy"]
    assert all(call["model_override"] == "gpt-oss:20b" for call in calls)
    assert all(call["num_predict"] == 4096 for call in calls)
    assert all(
        call["num_ctx_override"] == learning_curriculum_tasks.CURRICULUM_CONTEXT_TOKENS
        for call in calls
    )


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

    assert len(discovered) == 8
    assert model_calls[0]["purpose"] == "learning"
    assert model_calls[0]["model_override"] == db.get_settings()["learning_ollama_model"]
    assert model_calls[0]["num_predict"] == 4096
    assert model_calls[0]["num_ctx_override"] == learning_curriculum_tasks.CURRICULUM_CONTEXT_TOKENS
    assert result["source_count"] == 8
    assert result["queued_count"] == 1
    assert len(result["queued_job_ids"]) == 1
    path = learning.get_learning_path(result["path_id"])
    assert path["research"]["requested_topic"] == "موضوع فنی"
    assert path["research"]["model"] == db.get_settings()["learning_ollama_model"]
    assert path["research"]["model_selection"]["selected_model"] == db.get_settings()["learning_ollama_model"]
    assert path["research"]["outline_sha256"] == result["outline_sha256"]
    assert len(path["research"]["sources"]) == 8
    assert all(item["retrieved_at"] for item in path["research"]["sources"])
    assert all(item["publication_date"] is None and item["last_updated"] is None for item in path["research"]["sources"])
    assert len(path["topics"]) == len(_outline()["topics"])
    assert path["topics"][0]["status"] == "in_progress"
    assert all(item["status"] == "pending" for item in path["topics"][1:])
    assert context.checkpoint_data["phase"] == "curriculum_persisted"
    assert context.checkpoint_data["queries"] == learning_curriculum_tasks._research_queries("موضوع فنی")
    assert context.checkpoint_data["completed_queries"] == learning_curriculum_tasks._research_queries("موضوع فنی")

    retry_context = RecordingContext(task["id"], context.checkpoint_data)
    retried = learning_curriculum_tasks.handle_curriculum_task(
        task["payload"], context.checkpoint_data, retry_context
    )
    assert retried["path_id"] == result["path_id"]
    assert retried["outline_sha256"] == result["outline_sha256"]
    assert len(discovered) == 8
    # Eight full source bodies each span multiple evidence chunks; every chunk
    # must be reviewed before the curriculum can be finalized.
    assert len(model_calls) == learning_curriculum_tasks._source_evidence_batch_count(
        learning_curriculum_tasks._hydrate_source_bodies(task["id"], context.checkpoint_data["sources"])
    )
    assert len(model_calls) == 2
    assert retried["queue_status"] == "already_active"


def test_research_retains_more_than_three_sources_from_one_domain(learning_store, monkeypatch):
    calls = {"count": 0}

    def discover(_query, *, limit):
        calls["count"] += 1
        assert limit == 30
        return [
            web_sources.SourceDocument(
                url=f"https://community.example.test/guides/{calls['count']}/{index}",
                title=f"Community guide {calls['count']}-{index}",
                text=("Detailed community material with implementation notes. " * 8),
            )
            for index in range(4)
        ]

    monkeypatch.setattr(web_sources, "discover_sources", discover)
    context = RecordingContext()
    sources = learning_curriculum_tasks._collect_sources(
        "موضوع با منابع متعدد",
        topic_sha256="c" * 64,
        checkpoint=None,
        context=context,
    )

    assert calls["count"] == len(learning_curriculum_tasks._research_queries("موضوع با منابع متعدد"))
    assert len(sources) == calls["count"] * 4
    assert len({item["url"] for item in sources}) == len(sources)
    assert {item["url"].split("/")[2] for item in sources} == {"community.example.test"}


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


@pytest.mark.parametrize("discovered_sources", [[], [_source(1)]])
def test_research_retains_sparse_or_missing_sources_without_blocking_curriculum(
    learning_store, monkeypatch, discovered_sources
):
    monkeypatch.setattr(
        web_sources, "discover_sources",
        lambda *_args, **_kwargs: discovered_sources,
    )
    context = RecordingContext()
    sources = learning_curriculum_tasks._collect_sources(
        "موضوع کم‌منبع",
        topic_sha256="a" * 64,
        checkpoint=None,
        context=context,
    )
    assert len(sources) == len(discovered_sources)
    assert context.checkpoint_data["phase"] == "web_research"
    assert context.checkpoint_data["completed_queries"] == learning_curriculum_tasks._research_queries("موضوع کم‌منبع")


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
    runtime_settings_seen = {}
    def fake_runtime_limits(settings, *_args, **_kwargs):
        runtime_settings_seen.update(settings)
        return {"num_ctx": 8192, "cpu_threads": 1}
    monkeypatch.setattr(ollama, "runtime_limits", fake_runtime_limits)
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *_args, **_kwargs: "5m")

    def fake_request(path, payload=None, **_kwargs):
        if path == "/api/show":
            return {
                "model_info": {"llama.context_length": 8192},
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
        num_ctx_override=8192,
    )
    assert result == "{}"
    assert captured["options"]["num_predict"] == 800
    assert captured["options"]["num_ctx"] == 8192
    assert runtime_settings_seen["ollama_num_ctx"] == 8192
    assert captured["options"]["num_gpu"] == 0
    assert captured["model"] == "gpt-oss:20b"


def test_ollama_chat_fails_closed_when_hardware_cannot_provide_required_context(monkeypatch):
    from app import ollama

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
        "num_ctx": 4096, "cpu_threads": 1,
    })
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *_args, **_kwargs: "0")
    monkeypatch.setattr(ollama, "_request", lambda path, *_args, **_kwargs: {
        "model_info": {"llama.context_length": 8192},
        "details": {"family": "llama"},
    } if path == "/api/show" else pytest.fail("chat request must not run with a truncated context"))

    with pytest.raises(ollama.OllamaError, match="safe RAM budget"):
        ollama.chat(
            "Build a comprehensive course outline",
            purpose="learning",
            model_override="gpt-oss:20b",
            num_predict=4096,
            num_ctx_override=8192,
        )


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


def test_curriculum_generation_continues_beyond_forty_topics_without_truncating(learning_store, monkeypatch):
    monkeypatch.setattr(
        learning_curriculum_tasks, "get_settings",
        lambda: {"learning_ollama_model": "gpt-oss:20b"},
    )
    sources = [
        {"title": f"Official reference {i}", "url": f"https://docs{i}.example.test/ref",
         "excerpt": "Reference excerpt", "sha256": "a" * 64,
         "publication_date": None, "last_updated": None, "retrieved_at": "2026-11-11T00:00:00+00:00"}
        for i in range(1, 4)
    ]
    monkeypatch.setattr(learning_curriculum_tasks, "_collect_sources", lambda *_args, **_kwargs: sources)
    generated = []
    pages = []
    for batch in range(4):
        names = [f"Topic {batch * 24 + index + 1}" for index in range(24)]
        pages.append({
            "title": "Large technical course", "topics": names, "notes": "",
            "coverage_areas": [f"Area {batch}"], "has_more": True,
            "next_focus": f"Continue at area {batch + 1}",
            "topic_metadata": {
                name: {
                    "applicability": {"status": "unverified"},
                    "source_urls": [], "hierarchy_path": name, "depth": 0,
                } for name in names
            },
        })
    final_names = [f"Topic {96 + index + 1}" for index in range(4)]
    pages.append({
        "title": "Large technical course", "topics": final_names, "notes": "",
        "coverage_areas": ["Final area"], "has_more": False, "next_focus": "",
        "topic_metadata": {
            name: {
                "applicability": {"status": "unverified"},
                "source_urls": [], "hierarchy_path": name, "depth": 0,
            } for name in final_names
        },
    })

    def fake_generate(*_args, **_kwargs):
        page = pages[len(generated)]
        generated.append(page)
        return page, {"selected_model": "gpt-oss:20b"}

    monkeypatch.setattr(learning_curriculum_tasks, "_generate_outline", fake_generate)
    task = task_manager.create_task(
        learning_curriculum_tasks.CURRICULUM_TASK_TYPE,
        {"topic": "Large technical subject", "batch_size": 24, "source_text": ""},
        max_attempts=3,
    )
    result = learning_curriculum_tasks.handle_curriculum_task(
        task["payload"], None, RecordingContext(task["id"])
    )

    assert len(generated) == 5
    assert result["topic_count"] == 100
    path = learning.get_learning_path(result["path_id"])
    assert len(path["topics"]) == 100
    assert path["topics"][0]["topic"] == "Topic 1"
    assert path["topics"][-1]["topic"] == "Topic 100"
    with db.learning_connection() as connection:
        metadata = connection.execute(
            "SELECT applicability_json, source_urls_json FROM learning_path_topics WHERE path_id=? ORDER BY position LIMIT 1",
            (result["path_id"],),
        ).fetchone()
    assert json.loads(metadata["applicability_json"])["status"] == "unverified"
    assert json.loads(metadata["source_urls_json"]) == []


def test_topic_applicability_metadata_is_structured_and_not_overstated():
    value = _outline()
    topic = value["topics"][0]
    value["topic_metadata"] = {
        topic: {
            "hierarchy_path": "SQL Server / Query language / SELECT",
            "depth": 2,
            "source_urls": ["https://learn.microsoft.com/sql/t-sql/queries/select-transact-sql"],
            "applicability": {
                "status": "documented",
                "product": "SQL Server Database Engine",
                "versions": ["SQL Server 2022 (16.x)", "SQL Server 2025 (17.x)"],
                "builds": [],
                "editions": ["Standard", "Enterprise"],
                "compatibility_levels": ["160", "170"],
                "platforms": ["Windows"],
                "execution_modes": ["T-SQL batch"],
                "prerequisites": ["Database access"],
                "permissions": ["SELECT permission"],
                "limitations": ["Object-level permissions may further restrict access"],
                "notes": "Applies only to documented engine and database compatibility conditions.",
            },
        }
    }
    parsed = learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)
    metadata = parsed["topic_metadata"][topic]
    assert metadata["applicability"]["status"] == "documented"
    assert metadata["applicability"]["versions"] == ["SQL Server 2022 (16.x)", "SQL Server 2025 (17.x)"]
    assert metadata["hierarchy_path"] == "SQL Server / Query language / SELECT"
    assert metadata["depth"] == 2

    value["topic_metadata"][topic]["applicability"]["status"] = "verified"
    with pytest.raises(ValueError, match="status"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)


def test_curriculum_api_uses_batch_size_and_accepts_legacy_max_topics_alias():
    from app.main import LearningCurriculumRequest

    request = LearningCurriculumRequest(topic="SQL Server", batch_size=16)
    assert request.batch_size == 16
    assert request.max_topics is None

    legacy = LearningCurriculumRequest(topic="SQL Server", max_topics=40)
    assert legacy.batch_size == 24
    assert legacy.max_topics == 40



def test_release_years_and_dates_are_retained_and_chronologically_validated():
    value = _outline()
    topic = value["topics"][0]
    value["topic_metadata"] = {
        topic: {
            "applicability": {
                "status": "partially_documented",
                "versions": ["Product 1.0", "Product 2.0"],
                "release_years": ["2018", "2021", "2024"],
                "release_dates": ["2018-02-10", "2021-06-15", "2024-09-01"],
            },
            "source_urls": [],
            "hierarchy_path": topic,
            "depth": 0,
        }
    }
    parsed = learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)
    metadata = parsed["topic_metadata"][topic]["applicability"]
    assert metadata["release_years"] == ["2018", "2021", "2024"]
    assert metadata["release_dates"] == ["2018-02-10", "2021-06-15", "2024-09-01"]

    value["topic_metadata"][topic]["applicability"]["release_years"] = ["2021", "2018"]
    with pytest.raises(ValueError, match="ascending chronological order"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)

    value["topic_metadata"][topic]["applicability"]["release_years"] = ["2021", "20XX"]
    with pytest.raises(ValueError, match="invalid year/date"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)

    value["topic_metadata"][topic]["applicability"]["release_years"] = ["2021"]
    value["topic_metadata"][topic]["applicability"]["release_dates"] = ["2021-99-99"]
    with pytest.raises(ValueError, match="invalid calendar date"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)


def test_research_queries_include_release_history_and_current_updates():
    queries = learning_curriculum_tasks._research_queries("Python")
    assert any("first release initial stable version release dates" in item for item in queries)
    assert any("historical release archive yearly versions" in item for item in queries)
    assert any("latest release notes updates patches" in item for item in queries)
    watch_queries = learning_curriculum_tasks._release_watch_queries("Python")
    assert any("documentation corrections errata" in item for item in watch_queries)
    assert any("deprecation removed features end of life" in item for item in watch_queries)
    assert any("security advisory CVE" in item for item in watch_queries)



def test_release_watch_detects_changed_release_sources_and_enqueues_once(learning_store, monkeypatch):
    sources = [
        {
            "title": f"Release history {index}",
            "url": f"https://docs{index}.example.test/releases",
            "excerpt": (f"Release history and version details {index}. " * 20),
            "sha256": __import__("hashlib").sha256(
                (f"Release history and version details {index}. " * 30).encode("utf-8")
            ).hexdigest(),
            "publication_date": None,
            "last_updated": None,
            "retrieved_at": "2026-11-11T00:00:00+00:00",
        }
        for index in range(1, 4)
    ]
    path_id, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=9001,
        topic="Python",
        outline=_outline(),
        sources=sources,
        model="gpt-oss:20b",
        model_selection={"selected_model": "gpt-oss:20b"},
    )
    baseline = [_source(1), _source(2), _source(3)]
    current_docs = baseline
    monkeypatch.setattr(
        web_sources, "discover_sources",
        lambda *_args, **_kwargs: current_docs,
    )

    first_poll = learning_curriculum_tasks.maintain_release_watch(limit=3)
    assert first_poll == {"checked": 1, "queued": 0, "unchanged": 1, "errors": 0}

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_release_watch SET next_check_at=datetime('now', '-1 day') WHERE path_id=?",
            (path_id,),
        )
    current_docs = [
        _source(1),
        _source(2),
        web_sources.SourceDocument(
            url="https://python.example.test/releases/3",
            title="Python 3 release notes",
            text="New release notes and compatibility changes. " * 30,
        ),
    ]
    queued = []
    monkeypatch.setattr(
        learning_curriculum_tasks,
        "enqueue_curriculum_task",
        lambda topic, **kwargs: queued.append((topic, kwargs)) or {"id": 9002},
    )

    result = learning_curriculum_tasks.maintain_release_watch(limit=3)

    assert result == {"checked": 1, "queued": 1, "unchanged": 0, "errors": 0}
    assert queued and queued[0][0] == "Python"
    assert queued[0][1]["dedupe_key"].startswith(f"learning-release-watch:{path_id}:")
    assert queued[0][1]["refresh_of_path_id"] == path_id
    with db.learning_connection() as connection:
        watch = connection.execute(
            "SELECT last_task_id, last_source_fingerprint, status FROM learning_release_watch WHERE path_id=?",
            (path_id,),
        ).fetchone()
    assert watch["last_task_id"] == 9002
    assert len(watch["last_source_fingerprint"]) == 64
    assert watch["status"] == "active"


def test_release_watch_supersedes_previous_watch_without_mutating_old_curriculum(learning_store):
    sources = [
        {
            "title": f"Release history {index}",
            "url": f"https://docs{index}.example.test/releases",
            "excerpt": (f"Release history and version details {index}. " * 20),
            "sha256": __import__("hashlib").sha256(
                (f"Release history and version details {index}. " * 30).encode("utf-8")
            ).hexdigest(),
            "publication_date": None,
            "last_updated": None,
            "retrieved_at": "2026-11-11T00:00:00+00:00",
        }
        for index in range(1, 4)
    ]
    first, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=9101, topic="SQL Server", outline=_outline(), sources=sources,
        model="gpt-oss:20b", model_selection={"selected_model": "gpt-oss:20b"},
    )
    second, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=9102, topic="SQL Server", outline=_outline(), sources=sources,
        model="gpt-oss:20b", model_selection={"selected_model": "gpt-oss:20b"},
        refresh_of_path_id=first,
    )
    with db.learning_connection() as connection:
        statuses = dict(connection.execute(
            "SELECT path_id, status FROM learning_release_watch WHERE path_id IN (?, ?)",
            (first, second),
        ).fetchall())
        old_path = connection.execute(
            "SELECT title FROM learning_paths WHERE id=?",
            (first,),
        ).fetchone()
    assert statuses == {first: "superseded", second: "active"}
    assert old_path is not None



def test_versioned_curriculum_topics_must_be_oldest_to_newest():
    value = _outline()
    first, second = value["topics"][:2]
    value["topic_metadata"] = {
        first: {
            "applicability": {
                "status": "unverified",
                "versions": ["Python 3.8", "Python 3.9"],
                "release_years": ["2019", "2020"],
            },
            "source_urls": [],
            "hierarchy_path": first,
            "depth": 0,
        },
        second: {
            "applicability": {
                "status": "unverified",
                "versions": ["Python 3.10"],
                "release_years": ["2021"],
            },
            "source_urls": [],
            "hierarchy_path": second,
            "depth": 0,
        },
    }
    parsed = learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)
    assert parsed["topics"][:2] == [first, second]

    value["topic_metadata"][first]["applicability"]["versions"] = [
        "Python 3.9", "Python 3.8"
    ]
    with pytest.raises(ValueError, match="versions must be ordered"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)

    value["topic_metadata"][first]["applicability"]["versions"] = ["Python 3.8"]
    value["topic_metadata"][first]["applicability"]["release_years"] = ["2022"]
    value["topic_metadata"][second]["applicability"]["release_years"] = ["2021"]
    with pytest.raises(ValueError, match="oldest-to-newest"):
        learning_curriculum_tasks._parse_outline(json.dumps(value, ensure_ascii=False), 20)


def test_complete_curriculum_rejects_out_of_order_versions_across_batches():
    topics = ["Python 3.8 fundamentals", "Python 3.12 fundamentals"]
    metadata = {
        topics[0]: {
            "applicability": {"versions": ["Python 3.8"], "release_years": ["2019"]}
        },
        topics[1]: {
            "applicability": {"versions": ["Python 3.12"], "release_years": ["2023"]}
        },
    }

    # Each batch can be individually valid, but the merged curriculum must
    # preserve global oldest-to-newest ordering.
    learning_curriculum_tasks._validate_chronological_topics(topics, metadata)
    with pytest.raises(ValueError, match="oldest-to-newest"):
        learning_curriculum_tasks._validate_chronological_topics(
            list(reversed(topics)), metadata
        )


def test_legacy_research_checkpoint_without_query_provenance_is_researched_again():
    queries = learning_curriculum_tasks._research_queries("Python")
    checkpoint = {
        "topic_sha256": "a" * 64,
        "queries": queries,
        "completed_queries": queries,
        "sources": [{
            "title": "Old source",
            "url": "https://docs.example.test/old",
            "excerpt": "Legacy excerpt",
            "sha256": "b" * 64,
        }],
    }
    completed, sources = learning_curriculum_tasks._valid_checkpoint(
        checkpoint, topic_sha256="a" * 64, queries=queries
    )
    assert completed == []
    assert sources == []


def test_checkpoint_recovery_preserves_more_than_forty_sources():
    queries = learning_curriculum_tasks._research_queries("Python")
    sources = [
        {
            "title": f"Archived source {index}",
            "url": f"https://archive.example.test/releases/{index}",
            "excerpt": f"Historical release evidence {index}",
            "sha256": __import__("hashlib").sha256(str(index).encode()).hexdigest(),
            "query": queries[index % len(queries)],
        }
        for index in range(45)
    ]
    checkpoint = {
        "topic_sha256": "d" * 64,
        "queries": queries,
        "completed_queries": queries,
        "sources": sources,
    }

    completed, recovered = learning_curriculum_tasks._valid_checkpoint(
        checkpoint, topic_sha256="d" * 64, queries=queries
    )

    assert completed == queries
    assert len(recovered) == 45
    assert recovered[-1]["url"] == sources[-1]["url"]


def test_curriculum_prompt_includes_evidence_from_every_research_angle(monkeypatch):
    queries = learning_curriculum_tasks._research_queries("Python")
    sources = [
        {
            "title": f"Source {index}",
            "url": f"https://docs{index}.example.test/releases",
            "excerpt": f"Evidence for query {index}. " * 10,
            "sha256": __import__("hashlib").sha256(str(index).encode()).hexdigest(),
            "query": query,
        }
        for index, query in enumerate(queries, 1)
    ]
    captured = []
    monkeypatch.setattr(
        learning_curriculum_tasks, "get_settings",
        lambda: {"learning_ollama_model": "gpt-oss:20b"},
    )
    monkeypatch.setattr(
        learning, "_select_learning_generation_model",
        lambda *_args, **_kwargs: ("gpt-oss:20b", {"selected_model": "gpt-oss:20b"}),
    )
    monkeypatch.setattr(
        learning, "_prior_assistant_observations",
        lambda *_args, **_kwargs: {
            "items": [{"candidate_id": 8, "content": "Prior Python answer", "status": "unverified"}],
            "matched_count": 1, "included_count": 1, "omitted_count": 0,
            "status": "available",
        },
    )
    monkeypatch.setattr(
        learning_curriculum_tasks.ollama, "chat",
        lambda message, **_kwargs: captured.append(json.loads(message)) or json.dumps(_outline(), ensure_ascii=False),
    )

    learning_curriculum_tasks._generate_outline(
        "Python", 20, "", sources, "gpt-oss:20b"
    )

    included = captured[0]["research_sources"]
    assert captured[0]["prior_assistant_observations"]["items"][0]["status"] == "unverified"
    assert len(included) == len(queries)
    assert {item["research_query"] for item in included} == set(queries)
    assert all(item["excerpt"] for item in included)
    assert all(item["content_chunk_index"] == 1 for item in included)
    assert all(item["content_chunk_count"] == 1 for item in included)
    assert all(item["content_complete"] is False for item in included)
    requirements = captured[0]["requirements"]
    assert any("delivered in sequential content chunks" in item for item in requirements)
    assert any("regardless of publisher authority" in item for item in requirements)
    assert any("Do not discard unofficial" in item for item in requirements)
    assert any("every relevant source class" in item for item in requirements)
    assert not any("only when authoritative sources establish them" in item for item in requirements)




def test_every_retained_source_is_reviewed_across_resumable_curriculum_batches(learning_store, monkeypatch):
    queries = learning_curriculum_tasks._research_queries("Python")
    sources = [
        {
            "title": f"Evidence source {index}",
            "url": f"https://archive.example.test/source/{index}",
            "excerpt": f"Release and compatibility evidence {index}. " * 8,
            "sha256": __import__("hashlib").sha256(str(index).encode()).hexdigest(),
            "publication_date": None,
            "last_updated": None,
            "retrieved_at": "2026-11-11T00:00:00+00:00",
            "query": queries[index % len(queries)],
        }
        for index in range(17)
    ]
    monkeypatch.setattr(
        learning_curriculum_tasks, "get_settings",
        lambda: {"learning_ollama_model": "gpt-oss:20b"},
    )
    monkeypatch.setattr(
        learning_curriculum_tasks, "_collect_sources",
        lambda *_args, **_kwargs: sources,
    )
    monkeypatch.setattr(
        learning, "start_researched_path_learning",
        lambda _path_id, allow_retry=False: {"queued_count": 0, "job_ids": [], "status": "complete"},
    )
    topic_pages = []
    for batch in range(2):
        names = [f"Curriculum topic {batch * 12 + index + 1}" for index in range(12)]
        topic_pages.append({
            "title": "Complete Python curriculum",
            "topics": names,
            "notes": "",
            "coverage_areas": [f"Coverage {batch}"],
            "topic_metadata": {
                name: {
                    "applicability": {"status": "unverified"},
                    "source_urls": [],
                    "hierarchy_path": name,
                    "depth": 0,
                }
                for name in names
            },
            # Deliberately claim completion early; source pagination must override it.
            "has_more": False,
            "next_focus": "",
        })
    final_page = {
        "title": "Complete Python curriculum",
        "topics": topic_pages[0]["topics"] + topic_pages[1]["topics"],
        "notes": "",
        "coverage_areas": ["Final evidence review"],
        "topic_metadata": {
            **topic_pages[0]["topic_metadata"],
            **topic_pages[1]["topic_metadata"],
        },
        "has_more": False,
        "next_focus": "",
    }
    calls = []
    def fake_generate(_topic, _size, _source_text, retained_sources, model, *, batch_index=0, **_kwargs):
        calls.append({
            "batch_index": batch_index,
            "model": model,
            "source_urls": [
                item["url"]
                for item in learning_curriculum_tasks._source_evidence_batch(retained_sources, batch_index)
            ],
        })
        page = topic_pages[batch_index] if batch_index < 2 else final_page
        return page, {"selected_model": "gpt-oss:20b"}

    monkeypatch.setattr(learning_curriculum_tasks, "_generate_outline", fake_generate)
    task = task_manager.create_task(
        learning_curriculum_tasks.CURRICULUM_TASK_TYPE,
        {"topic": "Python", "batch_size": 24, "source_text": ""},
        max_attempts=3,
    )
    result = learning_curriculum_tasks.handle_curriculum_task(
        task["payload"], None, RecordingContext(task["id"])
    )

    reviewed_urls = [url for call in calls for url in call["source_urls"]]
    assert [call["batch_index"] for call in calls] == [0, 1, 2]
    assert len(reviewed_urls) == len(sources)
    assert set(reviewed_urls) == {item["url"] for item in sources}
    assert len(set(reviewed_urls)) == len(sources)
    assert all(call["model"] == "gpt-oss:20b" for call in calls)
    assert result["topic_count"] == 24


def test_source_evidence_batches_are_round_robin_and_unbounded_by_total_source_count():
    queries = learning_curriculum_tasks._research_queries("SQL Server")
    sources = [
        {
            "url": f"https://sources.example.test/{index}",
            "title": f"Source {index}",
            "excerpt": f"Evidence excerpt for source {index}.",
            "query": queries[index % len(queries)],
        }
        for index in range(103)
    ]

    batches = [
        learning_curriculum_tasks._source_evidence_batch(sources, index)
        for index in range(learning_curriculum_tasks._source_evidence_batch_count(sources))
    ]
    flattened = [item["url"] for batch in batches for item in batch]

    assert len(batches) == 13
    assert all(len(batch) <= learning_curriculum_tasks.MAX_PROMPT_SOURCE_EXCERPTS for batch in batches)
    assert len(flattened) == len(sources)
    assert len(set(flattened)) == len(sources)
    assert set(flattened) == {item["url"] for item in sources}
    assert len({item["query"] for item in batches[0]}) == len(queries)


def test_topic_metadata_does_not_truncate_long_release_histories_or_source_lists():
    value = _outline()
    topic = value["topics"][0]
    years = [str(year) for year in range(1980, 2025)]
    versions = [f"Product {index}.0" for index in range(1, 46)]
    urls = [f"https://archive.example.test/release/{index}" for index in range(45)]
    value["topic_metadata"] = {
        topic: {
            "applicability": {
                "status": "partially_documented",
                "versions": versions,
                "release_years": years,
            },
            "source_urls": urls,
            "hierarchy_path": topic,
            "depth": 0,
        }
    }

    parsed = learning_curriculum_tasks._parse_outline(
        json.dumps(value, ensure_ascii=False), 20
    )

    metadata = parsed["topic_metadata"][topic]
    assert metadata["applicability"]["versions"] == versions
    assert metadata["applicability"]["release_years"] == years
    assert metadata["source_urls"] == urls


def test_complete_source_bodies_are_persisted_and_every_text_chunk_is_reviewable(learning_store):
    source = {
        "title": "Full release archive",
        "url": "https://archive.example.test/releases",
        "sha256": __import__("hashlib").sha256(
            (("A" * 1000) + ("B" * 1000) + ("C" * 500)).encode()
        ).hexdigest(),
        "query": "Python release history",
        "publication_date": "1994-01-01",
        "last_updated": "2026-01-01",
        "retrieved_at": "2026-11-11T00:00:00+00:00",
        "excerpt": "A" * 1000,
    }
    body = ("A" * 1000) + ("B" * 1000) + ("C" * 500)

    learning_curriculum_tasks._persist_source_body(77, source, body)
    hydrated = learning_curriculum_tasks._hydrate_source_bodies(77, [source])
    chunks = learning_curriculum_tasks._ordered_source_evidence(hydrated)

    assert hydrated[0]["content"] == body
    assert hydrated[0]["content_complete"] is True
    assert [item["content_chunk_index"] for item in chunks] == [1, 2, 3]
    assert [item["content_chunk_count"] for item in chunks] == [3, 3, 3]
    assert "".join(item["excerpt"] for item in chunks) == body
    assert learning_curriculum_tasks._source_evidence_batch_count(hydrated) == 1
    with db.learning_connection() as connection:
        stored = connection.execute(
            """SELECT source_url, content_sha256, content, path_id
               FROM learning_research_source_text WHERE task_id=?""",
            (77,),
        ).fetchone()
    assert stored["source_url"] == source["url"]
    assert stored["content_sha256"] == source["sha256"]
    assert stored["content"] == body
    assert stored["path_id"] is None


def test_release_date_chronology_takes_precedence_over_version_labels():
    topics = ["SQL Server release A", "SQL Server release B"]
    metadata = {
        topics[0]: {
            "applicability": {
                "product": "SQL Server",
                "versions": ["SQL Server 2022"],
                "release_years": ["2022"],
                "release_dates": ["2022-11-16"],
            }
        },
        topics[1]: {
            "applicability": {
                "product": "SQL Server",
                "versions": ["SQL Server 2025"],
                "release_years": ["2025"],
                "release_dates": ["2025-11-18"],
            }
        },
    }

    learning_curriculum_tasks._validate_chronological_topics(topics, metadata)
    metadata[topics[0]]["applicability"]["release_dates"] = ["2025-11-18"]
    metadata[topics[0]]["applicability"]["release_years"] = ["2025"]
    metadata[topics[1]]["applicability"]["release_dates"] = ["2022-11-16"]
    metadata[topics[1]]["applicability"]["release_years"] = ["2022"]

    with pytest.raises(ValueError, match="release date/year"):
        learning_curriculum_tasks._validate_chronological_topics(topics, metadata)


def test_semantic_version_prereleases_sort_before_their_stable_release():
    prerelease = learning_curriculum_tasks._version_order_key("Python 3.13.0rc1")
    stable = learning_curriculum_tasks._version_order_key("Python 3.13.0")
    assert prerelease is not None and stable is not None
    assert prerelease < stable
    assert not stable < prerelease


def test_release_ledger_parser_preserves_versions_sources_and_incomplete_gaps():
    value = _outline()
    value["subject_map"] = [
        "Database systems/Relational databases/SQL Server",
        "Database systems/Relational databases/SQL Server/Backup and restore",
    ]
    value["release_ledger"] = [
        {
            "product": "SQL Server",
            "version": "2000",
            "release_date": "2000-11-30",
            "release_year": "2000",
            "release_type": "major",
            "platform": "Windows",
            "edition": "Standard",
            "source_urls": ["https://archive.example.test/sql/2000"],
            "status": "partially_documented",
            "notes": "Historical release information is incomplete.",
        },
        {
            "product": "SQL Server",
            "version": "2025",
            "release_date": "2025-11-18",
            "release_year": "2025",
            "release_type": "major",
            "platform": "Windows",
            "edition": "Standard",
            "source_urls": ["https://learn.example.test/sql/2025"],
            "status": "documented",
            "notes": "Current major release.",
        },
    ]
    value["unresolved_gaps"] = ["Historical cumulative update archive is incomplete."]
    value["coverage_audit"] = {
        "status": "partial",
        "earliest_release": "2000",
        "latest_release": "2025",
        "missing_coverage_areas": ["Legacy service packs"],
        "missing_release_lines": ["Some historical cumulative updates"],
        "prerequisite_gaps": [],
        "notes": "Historical records remain incomplete.",
    }

    parsed = learning_curriculum_tasks._parse_outline(
        json.dumps(value, ensure_ascii=False), 20
    )

    assert parsed["subject_map"] == value["subject_map"]
    assert [item["version"] for item in parsed["release_ledger"]] == ["2000", "2025"]
    assert parsed["release_ledger"][0]["release_date"] == "2000-11-30"
    assert parsed["release_ledger"][1]["source_urls"] == ["https://learn.example.test/sql/2025"]
    assert parsed["unresolved_gaps"] == value["unresolved_gaps"]
    assert parsed["coverage_audit"]["status"] == "partial"
    assert parsed["coverage_audit"]["missing_release_lines"] == ["Some historical cumulative updates"]


def test_researched_path_exposes_durable_subject_map_release_ledger_and_gaps(learning_store):
    value = _outline()
    value["subject_map"] = ["Python/Language fundamentals", "Python/Standard library"]
    value["release_ledger"] = [{
        "product": "Python",
        "version": "3.14",
        "release_date": "2025-10-07",
        "release_year": "2025",
        "release_type": "major",
        "platform": "All",
        "edition": "CPython",
        "source_urls": ["https://docs.python.org/3/whatsnew/3.14.html"],
        "status": "documented",
        "notes": "Python 3.14 release.",
    }]
    value["unresolved_gaps"] = ["Some third-party implementation differences remain."]
    value["coverage_audit"] = {
        "status": "partial",
        "earliest_release": "1991",
        "latest_release": "2025",
        "missing_coverage_areas": [],
        "missing_release_lines": [],
        "prerequisite_gaps": ["Alternative Python implementations"],
        "notes": "Coverage remains in progress.",
    }
    parsed = learning_curriculum_tasks._parse_outline(
        json.dumps(value, ensure_ascii=False), 20
    )
    path_id, _ = learning_curriculum_tasks._persist_researched_path(
        task_id=9901,
        topic="Python",
        outline=parsed,
        sources=[{
            "title": f"Python release notes {index}",
            "url": f"https://docs.python.org/3/whatsnew/3.14/{index}.html",
            "excerpt": "Python 3.14 release notes.",
            "sha256": str(index) * 64,
        } for index in range(1, 4)],
        model="gpt-oss:20b",
        model_selection={"status": "configured", "selected_model": "gpt-oss:20b"},
    )

    research = learning.get_learning_path(path_id)["research"]
    assert research["coverage"]["subject_map"] == value["subject_map"]
    assert research["coverage"]["release_ledger"][0]["version"] == "3.14"
    assert research["coverage"]["unresolved_gaps"] == value["unresolved_gaps"]
    assert research["coverage"]["coverage_audit"]["status"] == "partial"


def test_short_incomplete_release_note_is_retained_as_research_evidence(learning_store, monkeypatch):
    calls = {"count": 0}

    def discover(_query, *, limit):
        calls["count"] += 1
        if calls["count"] == 1:
            return [web_sources.SourceDocument(
                url="https://community.example.test/errata/short",
                title="Short community erratum",
                text="Patch note: fixes a connection retry regression.",
            )]
        return []

    monkeypatch.setattr(web_sources, "discover_sources", discover)
    sources = learning_curriculum_tasks._collect_sources(
        "Database connection retries",
        topic_sha256="d" * 64,
        checkpoint=None,
        context=RecordingContext(),
    )

    assert len(sources) == 1
    assert sources[0]["title"] == "Short community erratum"
    assert sources[0]["excerpt"] == "Patch note: fixes a connection retry regression."
