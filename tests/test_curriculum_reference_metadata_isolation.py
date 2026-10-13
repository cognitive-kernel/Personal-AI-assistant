from copy import deepcopy

from app.curricula import CURRICULA, get_curriculum, list_curricula


def _replace_sql_server_references(monkeypatch):
    references = [
        {
            "title": "Test reference",
            "url": "https://docs.example.test/sql",
            "description": "Isolated test metadata",
        }
    ]
    monkeypatch.setitem(CURRICULA["sql-server"], "reference_urls", references)
    return deepcopy(references)


def test_get_curriculum_does_not_expose_mutable_reference_metadata(monkeypatch):
    expected = _replace_sql_server_references(monkeypatch)

    result = get_curriculum("sql-server")
    result["reference_urls"][0]["url"] = "https://attacker.example/changed"
    result["reference_urls"].append(
        {"title": "Injected", "url": "https://attacker.example/"}
    )

    assert get_curriculum("sql-server")["reference_urls"] == expected


def test_list_curricula_does_not_expose_nested_reference_metadata(monkeypatch):
    expected = _replace_sql_server_references(monkeypatch)

    result = next(item for item in list_curricula() if item["slug"] == "sql-server")
    result["reference_urls"][0]["url"] = "https://attacker.example/changed"

    fresh = next(item for item in list_curricula() if item["slug"] == "sql-server")
    assert fresh["reference_urls"] == expected
