from urllib.parse import urlsplit

from app.curricula import CURRICULA


def test_curriculum_catalog_has_valid_topic_and_prerequisite_shapes():
    assert CURRICULA

    for slug, curriculum in CURRICULA.items():
        assert isinstance(slug, str) and slug.strip()
        assert isinstance(curriculum.get("title"), str) and curriculum["title"].strip()
        assert isinstance(curriculum.get("category"), str) and curriculum["category"].strip()

        prerequisites = curriculum.get("prerequisites")
        assert isinstance(prerequisites, list), slug
        assert all(isinstance(item, str) and item.strip() for item in prerequisites), slug

        topics = curriculum.get("topics")
        assert isinstance(topics, list) and topics, slug
        assert all(isinstance(topic, str) and topic.strip() for topic in topics), slug


def test_declared_curriculum_references_are_safe_https_urls():
    for slug, curriculum in CURRICULA.items():
        references = curriculum.get("reference_urls", [])
        assert isinstance(references, list), slug

        for reference in references:
            assert isinstance(reference, dict), slug
            assert isinstance(reference.get("title"), str) and reference["title"].strip(), slug
            assert isinstance(reference.get("url"), str) and reference["url"].strip(), slug

            parsed = urlsplit(reference["url"])
            assert parsed.scheme == "https", (slug, reference["url"])
            assert parsed.hostname, (slug, reference["url"])
            assert parsed.username is None and parsed.password is None, (slug, reference["url"])


def test_curriculum_levels_preserve_every_topic_in_order():
    from app.curricula import get_curriculum

    for slug, catalog_entry in CURRICULA.items():
        curriculum = get_curriculum(slug)
        assert curriculum is not None, slug

        levels = curriculum.get("levels")
        assert [level["level"] for level in levels] == [
            "beginner", "intermediate", "advanced"
        ], slug
        flattened_topics = [
            topic for level in levels for topic in level["topics"]
        ]
        assert flattened_topics == catalog_entry["topics"], slug
