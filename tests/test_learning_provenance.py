from app.learning import _source_attribution_passes


def test_source_attribution_requires_exact_retrieved_url():
    sources = [{"url": "https://docs.python.org/3/library/json.html"}]
    assert _source_attribution_passes(
        "Sources: https://docs.python.org/3/library/json.html", sources
    )
    assert not _source_attribution_passes("A source was consulted.", sources)
    assert _source_attribution_passes("Model-only explanation.", [])
