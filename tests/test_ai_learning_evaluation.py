from app.ai_learning.evaluation import (
    evaluate_answer,
    evaluate_dataset,
    evaluate_retrieval_case,
    normalize_text,
)


def test_persian_normalization_handles_arabic_letters_and_diacritics():
    assert normalize_text("كِتاب يَادگيری") == normalize_text("کتاب یادگیری")


def test_answer_quality_checks_concepts_forbidden_claims_and_sources():
    answer = "در SQL Server، ایندکس به جست‌وجوی رکوردها کمک می‌کند. منبع: https://learn.microsoft.com/sql/"
    result = evaluate_answer(
        answer,
        expected_concepts=["ایندکس", "جست‌وجوی رکوردها"],
        must_not_claim=["همیشه سریع‌تر است"],
        expected_sources=["https://learn.microsoft.com/sql/"],
    )
    assert result["passed"] is True
    assert result["concept_recall"] == 1.0
    assert result["source_coverage"] == 1.0


def test_answer_quality_rejects_missing_concepts_forbidden_claims_and_short_answers():
    result = evaluate_answer(
        "ایندکس همیشه سریع‌تر است",
        expected_concepts=["قفل‌گذاری"],
        must_not_claim=["همیشه سریع‌تر است"],
        min_chars=100,
    )
    assert result["passed"] is False
    assert result["substantive"] is False
    assert result["concept_recall"] == 0.0
    assert result["forbidden_claims"]


def test_retrieval_metrics_detect_missing_expected_and_unpublished_leakage():
    result = evaluate_retrieval_case(
        [{"chunk_id": 10}, {"chunk_id": 99}],
        expected_ids=[10, 11],
        forbidden_ids=[99],
    )
    assert result["passed"] is False
    assert result["missing_ids"] == [11]
    assert result["forbidden_ids_found"] == [99]
    assert result["recall"] == 0.5


def test_dataset_evaluation_runs_without_external_models():
    cases = [{
        "id": "persian-index",
        "query": "ایندکس در SQL Server چیست؟",
        "expected_chunk_ids": [1],
        "forbidden_chunk_ids": [2],
        "expected_concepts": ["ایندکس", "SQL Server"],
    }]
    report = evaluate_dataset(
        cases,
        retrieve_fn=lambda _query: [{"chunk_id": 1, "body": "published"}],
        answer_fn=lambda _query, _items: "ایندکس در SQL Server به جست‌وجوی داده کمک می‌کند.",
    )
    assert report["case_count"] == 1
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0
