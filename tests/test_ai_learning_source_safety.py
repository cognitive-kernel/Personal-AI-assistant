from __future__ import annotations

import pytest

from app.ai_learning import service
from app.ai_learning.db import connection, initialize_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()


def test_private_source_address_is_rejected_before_http_request(isolated_store, monkeypatch):
    course = service.create_course(title="ایمنی منابع")
    topic = service.add_topic(course["id"], "اعتبارسنجی منابع")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "پیوندها")
    version = service.add_content_version(
        lesson["id"],
        "# پیوندها\n\nبرای بررسی منبع از دامنه عمومی استفاده کنید و به نشانی خصوصی درخواست نفرستید.",
        raw_response="https://example.com",
    )
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO sources(content_version_id,title,url,publisher,source_type,validation_status)
               VALUES(?,?,?,?,?,'unverified')""",
            (version["id"], "Local target", "http://127.0.0.1/private", "127.0.0.1", "test"),
        )
        source_id = int(cursor.lastrowid)

    monkeypatch.setattr(
        service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="خصوصی یا غیرعمومی"):
        service.check_content_source(source_id)


def test_source_links_remain_unverified_until_explicit_validation(isolated_store):
    course = service.create_course(title="منابع و انتشار")
    topic = service.add_topic(course["id"], "مدیریت منابع")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "مستندات")
    version = service.add_content_version(
        lesson["id"],
        "# مستندات\n\nبرای جزئیات به [مستندات رسمی](https://example.com/docs) مراجعه کنید. "
        "این متن نمونه برای آزمون ثبت منبع است.",
        raw_response="متن پاسخ",
        source_kind="chatgpt_ui",
    )
    sources = service.list_content_sources(version["id"])
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.com/docs"
    assert sources[0]["validation_status"] == "unverified"


def test_unapproved_topic_cannot_publish_even_reviewed_content(isolated_store):
    course = service.create_course(title="انتشار کنترل‌شده")
    topic = service.add_topic(course["id"], "موضوع تأییدنشده")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "درس")
    version = service.add_content_version(
        lesson["id"],
        "# درس\n\nاین یک متن محتوایی معتبر برای آزمون است و نباید قبل از تأیید سرفصل منتشر شود.",
    )
    service.review_content(version["id"], "reviewed")
    with connection() as conn:
        conn.execute("UPDATE topics SET approval_status='pending' WHERE id=?", (topic["id"],))
    with pytest.raises(ValueError, match="Topic must be approved"):
        service.publish_content(version["id"])
    assert service.search_knowledge("محتوایی معتبر")["count"] == 0
