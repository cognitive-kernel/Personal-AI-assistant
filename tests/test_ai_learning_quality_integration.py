from __future__ import annotations

import pytest

from app.ai_learning import service
from app.ai_learning.db import initialize_store
from app.ai_learning.evaluation import evaluate_dataset


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()
    # Distinct deterministic vectors keep this test offline when adding the draft
    # lesson after a published lesson already exists.
    monkeypatch.setattr(
        service,
        "ollama_embed",
        lambda texts, model: [
            [1.0, 0.0] if "ایندکس" in text or "ایندكس" in text else [0.0, 1.0]
            for text in texts
        ],
    )


def test_quality_benchmark_uses_real_published_only_retrieval(isolated_store):
    course = service.create_course(title="ارزیابی کیفیت دانش")
    topic = service.add_topic(course["id"], "ایندکس")
    service.decide_topics([topic["id"]], "approved")
    published_lesson = service.create_lesson(topic["id"], "ایندکس در SQL Server")
    published_version = service.add_content_version(
        published_lesson["id"],
        "# ایندکس در SQL Server\n\nایندکس به موتور SQL Server کمک می‌کند رکوردها را سریع‌تر پیدا کند. "
        "مستندات: https://learn.microsoft.com/sql/",
    )
    service.review_content(published_version["id"], "reviewed")
    service.publish_content(published_version["id"])

    draft_lesson = service.create_lesson(topic["id"], "اطلاعات پیش‌نویس")
    service.add_content_version(
        draft_lesson["id"],
        "# عبارت محرمانه پیش‌نویس\n\nاین عبارت باید تا زمان انتشار در جست‌وجو ظاهر نشود: draft-only-needle-94731.",
    )

    expected = service.search_knowledge("ایندکس SQL Server")["items"]
    assert expected
    expected_ids = [int(item["chunk_id"]) for item in expected]
    report = evaluate_dataset(
        [{
            "id": "published-persian-index",
            "expected_language": "fa",
            "query": "ایندکس SQL Server",
            "expected_chunk_ids": expected_ids,
            "expected_concepts": ["ایندکس", "SQL Server"],
            "expected_sources": ["https://learn.microsoft.com/sql/"],
        }],
        retrieve_fn=lambda query: service.search_knowledge(query)["items"],
        answer_fn=lambda _query, _items: (
            "ایندکس در SQL Server به پیدا کردن رکوردها کمک می‌کند. "
            "مستندات: https://learn.microsoft.com/sql/"
        ),
    )
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0
    assert service.search_knowledge("draft-only-needle-94731")["count"] == 0
