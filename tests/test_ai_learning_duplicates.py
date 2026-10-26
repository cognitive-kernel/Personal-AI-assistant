import pytest

from app.ai_learning import db, service


@pytest.fixture
def learning_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai-learning.db"))
    db.initialize_store()


def test_course_titles_are_unique_after_persian_normalization(learning_store):
    service.create_course(title="SQL Server 2025")
    with pytest.raises(ValueError, match="عنوان دوره تکراری"):
        service.create_course(title="  sql-server   2025! ")


def test_topic_titles_are_unique_on_create_and_edit(learning_store, monkeypatch):
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: [
        [1.0, 0.0] if "ایندکس" in text else [0.0, 1.0] for text in texts
    ])
    course = service.create_course(title="دوره آزمایشی")
    first = service.add_topic(course["id"], "ایندکس‌ها در SQL Server")
    with pytest.raises(ValueError, match="عنوان تکراری"):
        service.add_topic(course["id"], "ایندکسها در SQL Server!")
    second = service.add_topic(course["id"], "پشتیبان‌گیری")
    with pytest.raises(ValueError, match="عنوان تکراری"):
        service.edit_topic(second["id"], title=" ایندکس‌ها در SQL Server ")
    assert service.get_course(course["id"])["topic_total"] == 2
    assert first["title"] == "ایندکس‌ها در SQL Server"


def test_outline_import_skips_punctuation_and_persian_letter_duplicates(learning_store):
    course = service.create_course(title="SQL Server")
    result = service.import_outline(
        course["id"],
        "1. ایندکس‌ها در SQL Server\n2. ایندکسها در SQL Server!\n3. پشتیبان‌گیری و بازیابی",
    )
    assert result["imported"] == 2
    assert result["skipped_existing"] == 1
    assert [item["title"] for item in result["topics"]] == [
        "ایندکس‌ها در SQL Server",
        "پشتیبان‌گیری و بازیابی",
    ]


def test_lesson_titles_are_unique_within_topic_on_create_and_edit(learning_store, monkeypatch):
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: [
        [1.0, 0.0] if "ایندکس" in text else [0.0, 1.0] for text in texts
    ])
    course = service.create_course(title="دوره درس‌ها")
    topic = service.add_topic(course["id"], "مبانی")
    service.decide_topics([topic["id"]], "approved")
    first = service.create_lesson(topic["id"], "مدیریت ایندکس‌ها")
    with pytest.raises(ValueError, match="عنوان تکراری"):
        service.create_lesson(topic["id"], "مدیریت ایندکسها!")
    second = service.create_lesson(topic["id"], "بازیابی اطلاعات")
    with pytest.raises(ValueError, match="عنوان تکراری"):
        service.edit_lesson(second["id"], " مدیریت ایندکس‌ها ")
    assert [item["title"] for item in service.list_lessons(topic["id"])] == [
        first["title"],
        "بازیابی اطلاعات",
    ]


def test_outline_parser_deduplicates_normalized_titles():
    titles = service._parse_outline_titles(
        "1. ایندکس‌ها در SQL Server\n2. ایندکسها در SQL Server!\n3. پشتیبان‌گیری"
    )
    assert titles == ["ایندکس‌ها در SQL Server", "پشتیبان‌گیری"]



def test_semantically_duplicate_lesson_content_is_detected_without_saving_again(
    learning_store, monkeypatch
):
    course = service.create_course(title="دوره محتوای درس")
    first_topic = service.add_topic(course["id"], "موضوع اول")
    second_topic = service.add_topic(course["id"], "موضوع دوم")
    service.decide_topics([first_topic["id"], second_topic["id"]], "approved")
    lesson = service.create_lesson(first_topic["id"], "درس اول")
    existing_body = "An index helps SQL Server locate rows efficiently."
    candidate_body = "SQL Server uses indexes to find rows more efficiently."
    digest = service.hashlib.sha256(existing_body.encode("utf-8")).hexdigest()
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO content_versions(
                   lesson_id,version_no,markdown,raw_response,content_sha256,
                   review_status,source_kind,provider
               ) VALUES(?,1,?,?,?,'needs_review','manual','test')""",
            (lesson["id"], existing_body, existing_body, digest),
        )
        monkeypatch.setattr(
            service,
            "ollama_embed",
            lambda texts, model: [[1.0, 0.0] for _ in texts],
        )
        duplicate = service._find_semantic_duplicate_content(
            conn, candidate_body, exclude_topic_id=second_topic["id"]
        )
    assert duplicate is not None
    assert duplicate["method"] == "embedding"
    assert duplicate["topic_id"] == first_topic["id"]



def test_outline_import_skips_semantically_duplicate_topic_titles(learning_store, monkeypatch):
    course = service.create_course(title="SQL Server")
    existing = service.add_topic(course["id"], "مدیریت ایندکس‌های ترکیبی در SQL Server")
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: [
        [0.0, 1.0] if "موضوع" in text else [1.0, 0.0] for text in texts
    ])

    result = service.import_outline(
        course["id"],
        "1. مدیریت ایندکس‌های ترکیبی برای SQL Server\n"
        "2. آموزش موضوع کاملاً تازه",
    )

    assert result["imported"] == 1
    assert result["skipped_existing"] == 1
    assert result["skipped_duplicates"][0]["duplicate_of"] == existing["title"]
    assert result["skipped_duplicates"][0]["method"] == "embedding+token_overlap"


def test_semantic_title_check_fails_closed_if_embedding_unavailable(learning_store, monkeypatch):
    course = service.create_course(title="SQL Server")
    service.add_topic(course["id"], "مبانی")
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: (_ for _ in ()).throw(RuntimeError("offline")))

    with pytest.raises(ValueError, match="بررسی معنایی عنوان‌های تکراری"):
        service.add_topic(course["id"], "موضوع دیگر")



def test_related_but_distinct_outline_topics_are_not_filtered(learning_store, monkeypatch):
    course = service.create_course(title="SQL Server")
    service.add_topic(course["id"], "طراحی ایندکس‌های SQL Server")
    monkeypatch.setattr(service, "ollama_embed", lambda texts, model: [[1.0, 0.0] for _ in texts])

    result = service.import_outline(
        course["id"],
        "1. تحلیل Execution Plan در SQL Server\n"
        "2. بهینه‌سازی پرس‌وجوهای SQL Server",
    )

    assert result["imported"] == 2
    assert result["skipped_existing"] == 0
