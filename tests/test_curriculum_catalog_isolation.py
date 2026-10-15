from app.curricula import CURRICULA, get_curriculum, list_curricula


def test_get_curriculum_returns_independent_topic_collections():
    expected_topics = list(CURRICULA["sql-server"]["topics"])
    expected_prerequisites = list(CURRICULA["sql-server"]["prerequisites"])

    result = get_curriculum("sql-server")
    result["topics"].append("Additional topic")
    result["prerequisites"].append("Additional prerequisite")
    result["levels"][0]["topics"].append("Additional level topic")

    assert CURRICULA["sql-server"]["topics"] == expected_topics
    assert CURRICULA["sql-server"]["prerequisites"] == expected_prerequisites

    fresh = get_curriculum("sql-server")
    assert fresh["topics"] == expected_topics
    assert fresh["prerequisites"] == expected_prerequisites
    assert all("Additional level topic" not in level["topics"] for level in fresh["levels"])


def test_list_curricula_returns_independent_prerequisites():
    expected = list(CURRICULA["sql-server"]["prerequisites"])

    result = next(item for item in list_curricula() if item["slug"] == "sql-server")
    result["prerequisites"].append("Additional prerequisite")

    fresh = next(item for item in list_curricula() if item["slug"] == "sql-server")
    assert fresh["prerequisites"] == expected
