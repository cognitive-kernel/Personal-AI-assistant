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


def test_topic_titles_are_unique_on_create_and_edit(learning_store):
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


def test_lesson_titles_are_unique_within_topic_on_create_and_edit(learning_store):
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
