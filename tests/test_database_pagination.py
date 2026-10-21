from app import db, learning, pagination


def configure_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()


def test_database_backed_learning_jobs_use_limit_and_offset(tmp_path, monkeypatch):
    configure_stores(tmp_path, monkeypatch)
    jobs = [learning.create_job(f"pagination fixture {index}") for index in range(6)]
    first = learning.list_jobs(limit=2, offset=0)
    second = learning.list_jobs(limit=2, offset=2)
    last = learning.list_jobs(limit=2, offset=4)
    assert [item["id"] for item in first] == [jobs[5]["id"], jobs[4]["id"]]
    assert [item["id"] for item in second] == [jobs[3]["id"], jobs[2]["id"]]
    assert [item["id"] for item in last] == [jobs[1]["id"], jobs[0]["id"]]


def test_learning_paths_query_only_requested_database_page(tmp_path, monkeypatch):
    configure_stores(tmp_path, monkeypatch)
    with db.learning_connection() as connection:
        for index in range(6):
            connection.execute(
                "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
                (f"page-fixture-{index}", f"Page fixture {index}"),
            )
    first = learning.list_learning_paths(limit=2, offset=0)
    second = learning.list_learning_paths(limit=2, offset=2)
    assert len(first) == len(second) == 2
    assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})


def test_pagination_metadata_reports_navigation():
    first = pagination.metadata(page=1, page_size=10, total=21)
    middle = pagination.metadata(page=2, page_size=10, total=21)
    last = pagination.metadata(page=3, page_size=10, total=21)
    assert first["has_previous"] is False and first["has_next"] is True
    assert middle["has_previous"] is True and middle["has_next"] is True
    assert last["has_previous"] is True and last["has_next"] is False
