from __future__ import annotations

from app.ai_learning.evaluation import evaluate_dataset, normalize_text


def test_bilingual_quality_benchmark_is_deterministic_and_rejects_forbidden_claims():
    cases = [
        {
            "id": "fa-sql-index",
            "expected_language": "fa",
            "query": "ایندکس در SQL Server چیست؟",
            "expected_chunk_ids": [101],
            "forbidden_chunk_ids": [202],
            "expected_concepts": ["ایندکس", "SQL Server"],
            "must_not_claim": ["ایندکس همیشه سرعت همهٔ عملیات را زیاد می‌کند"],
            "expected_sources": ["https://learn.microsoft.com/sql/"],
        },
        {
            "id": "en-python-types",
            "expected_language": "en",
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



def test_bilingual_evaluator_rejects_answers_in_the_wrong_language():
    cases = [
        {
            "id": "fa-answer-returned-in-english",
            "query": "ایندکس در SQL Server چیست؟",
            "expected_chunk_ids": [1],
            "expected_language": "fa",
        },
        {
            "id": "en-answer-returned-in-persian",
            "query": "What do Python type hints do?",
            "expected_chunk_ids": [2],
            "expected_language": "en",
        },
    ]
    answers = {
        cases[0]["query"]: (
            "SQL Server indexing helps locate rows and improve query performance. "
            "Documentation: https://learn.microsoft.com/sql/"
        ),
        cases[1]["query"]: (
            "تایپ‌هینت‌ها نوع داده مورد انتظار را مشخص می‌کنند و به تحلیل ایستا کمک می‌کنند."
        ),
    }
    report = evaluate_dataset(
        cases,
        retrieve_fn=lambda query: [{"chunk_id": 1 if query == cases[0]["query"] else 2}],
        answer_fn=lambda query, _items: answers[query],
    )
    assert report["failed_count"] == 2
    assert report["results"][0]["answer"]["language_adherence"]["reason"] == (
        "expected_language_not_dominant"
    )
    assert report["results"][1]["answer"]["language_adherence"]["reason"] == (
        "expected_language_not_dominant"
    )


def test_language_adherence_ignores_urls_and_code_blocks():
    from app.ai_learning.evaluation import evaluate_language_adherence

    result = evaluate_language_adherence(
        "در SQL Server ایندکس به جست‌وجوی داده کمک می‌کند. "
        "```sql\nSELECT * FROM dbo.Items;\n``` "
        "منابع: https://learn.microsoft.com/sql/",
        "fa",
    )
    assert result["passed"] is True
    assert result["dominant_script"] == "arabic-script"
    assert result["latin_letter_count"] == len("SQLServer")


def test_language_adherence_fails_closed_when_there_is_not_enough_prose():
    from app.ai_learning.evaluation import evaluate_language_adherence

    result = evaluate_language_adherence("SQL `SELECT * FROM t` https://example.com", "fa")
    assert result["passed"] is False
    assert result["detected_language"] == "unknown"
    assert result["reason"] == "insufficient_script_evidence"


def test_language_adherence_rejects_unsupported_expected_language():
    import pytest

    from app.ai_learning.evaluation import evaluate_language_adherence

    with pytest.raises(ValueError, match="Persian.*English"):
        evaluate_language_adherence("Bonjour tout le monde", "fr")

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
