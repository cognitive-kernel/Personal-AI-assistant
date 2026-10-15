import app.curricula_expansion as expansion


def test_curriculum_expansion_ignores_non_string_and_blank_topics(monkeypatch):
    monkeypatch.setattr(
        expansion,
        "CURRICULUM_EXPANSIONS",
        {
            "known": [None, 7, {}, "", "   ", "Valid topic", "valid TOPIC"],
            "unknown": ["Must not create a catalog entry"],
        },
    )
    catalog = {"known": {"topics": ["Existing topic"]}}

    expansion.extend_curricula(catalog)

    assert catalog == {
        "known": {"topics": ["Existing topic", "Valid topic"]},
    }
