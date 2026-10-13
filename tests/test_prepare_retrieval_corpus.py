import json

import pytest

from app.retrieval_evaluation import load_evaluation_cases, relevant_source_urls


def test_relevant_source_urls_returns_sorted_unique_ids():
    cases = [
        {"query": "one", "relevant_ids": ["https://example.com/b", "https://example.com/a"]},
        {"query": "two", "relevant_ids": ["https://example.com/a"]},
    ]

    assert relevant_source_urls(cases) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


@pytest.mark.parametrize(
    "cases",
    [
        [],
        ["not an object"],
        [{"query": "missing labels"}],
        [{"relevant_ids": []}],
        [{"relevant_ids": [""]}],
        [{"relevant_ids": ["   "]}],
        [{"relevant_ids": [None]}],
        [{"relevant_ids": [123]}],
    ],
)
def test_relevant_source_urls_rejects_invalid_cases(cases):
    with pytest.raises(ValueError):
        relevant_source_urls(cases)


def test_load_evaluation_cases_reads_json_array(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps([{"query": "q", "relevant_ids": ["https://example.com"]}]),
        encoding="utf-8",
    )

    assert load_evaluation_cases(dataset) == [
        {"query": "q", "relevant_ids": ["https://example.com"]}
    ]


def test_load_evaluation_cases_rejects_non_array(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text('{"query": "q"}', encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array"):
        load_evaluation_cases(dataset)
