from __future__ import annotations

from app.ai_learning.evaluation import evaluate_dataset, normalize_text


def test_bilingual_quality_benchmark_is_deterministic_and_rejects_forbidden_claims():
    cases = [
        {
            "id": "fa-sql-index",
            "query": "ایندکس در SQL Server چیست؟",
            "expected_chunk_ids": [101],
            "forbidden_chunk_ids": [202],
            "expected_concepts": ["ایندکس", "SQL Server"],
            "must_not_claim": ["ایندکس همیشه سرعت همهٔ عملیات را زیاد می‌کند"],
            "expected_sources": ["https://learn.microsoft.com/sql/"],
        },
        {
            "id": "en-python-types",
            "query": "What do Python type hints do?",
            "expected_chunk_ids": [303],
            "forbidden_chunk_ids": [404],
            "expected_concepts": ["type hints", "static analysis"],
            "must_not_claim": ["type hints are always enforced at runtime"],
            "expected_sources": ["https://docs.python.org/3/library/typing.html"],
        },
    ]
    retrieved = {
        "ایندکس در SQL Server چیست؟": [{"chunk_id": 101}],
        "What do Python type hints do?": [{"chunk_id": 303}],
    }
    answers = {
        "ایندکس در SQL Server چیست؟": (
            "ایندکس در SQL Server برای پیدا کردن رکوردها مفید است. "
            "مستندات: https://learn.microsoft.com/sql/"
        ),
        "What do Python type hints do?": (
            "Python type hints describe expected types and can support static analysis. "
            "Docs: https://docs.python.org/3/library/typing.html"
        ),
    }
    report = evaluate_dataset(
        cases,
        retrieve_fn=lambda query: retrieved[query],
        answer_fn=lambda query, _items: answers[query],
    )
    assert report["case_count"] == 2
    assert report["passed_count"] == 2
    assert report["failed_count"] == 0
    assert report["pass_rate"] == 1.0


def test_bilingual_quality_evaluator_detects_forbidden_claim_and_persian_normalization():
    assert normalize_text("ي ك") == normalize_text("ی ک")
    report = evaluate_dataset(
        [{
            "id": "en-runtime-overclaim",
            "query": "Python typing",
            "expected_chunk_ids": [1],
            "expected_concepts": ["type hints"],
            "must_not_claim": ["always enforced at runtime"],
        }],
        retrieve_fn=lambda _query: [{"chunk_id": 1}],
        answer_fn=lambda _query, _items: "Type hints are always enforced at runtime.",
    )
    assert report["failed_count"] == 1
    assert report["results"][0]["answer"]["forbidden_claims"] == [
        "always enforced at runtime"
    ]
