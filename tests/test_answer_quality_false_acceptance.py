from app.answer_quality_evaluation import evaluate_automated_acceptance


def _result(*, prohibited_phrase_hits=None):
    return {
        "case_id": "case-1",
        "generation_status": "completed",
        "generation_seconds": 1.0,
        "surface_diagnostic_status": "completed",
        "surface_concept_coverage": 1.0,
        "prohibited_phrase_hits": prohibited_phrase_hits or [],
        "expected_language_script_present": True,
        "source_audit_status": "completed",
        "source_count": 2,
        "trusted_source_count": 1,
        "research_review_status": "passed_model_assisted",
        "unattributed_answer_url_count": 0,
    }


def test_prohibited_reference_phrase_is_rejected_and_counted_in_false_acceptance_denominator():
    report = evaluate_automated_acceptance([
        _result(prohibited_phrase_hits=["retrieval alone proves truth"])
    ])
    assert report["version"] == "bilingual-answer-gate-v2"
    assert report["failed_case_count"] == 1
    assert report["case_decisions"][0]["accepted"] is False
    assert report["false_acceptance"]["reference_negative_case_count"] == 1
    assert report["false_acceptance"]["before_content_filter"]["false_acceptance_count"] == 1
    assert report["false_acceptance"]["before_content_filter"]["false_acceptance_rate"] == 1.0
    assert report["false_acceptance"]["final_gate"]["false_acceptance_count"] == 0
    assert report["false_acceptance"]["final_gate"]["false_acceptance_rate"] == 0.0


def test_clean_case_has_no_claimed_false_acceptance_rate_without_negative_labels():
    report = evaluate_automated_acceptance([_result()])
    assert report["accepted_case_count"] == 1
    assert report["false_acceptance"]["reference_negative_case_count"] == 0
    assert report["false_acceptance"]["before_content_filter"]["false_acceptance_rate"] is None
    assert report["false_acceptance"]["final_gate"]["false_acceptance_rate"] is None
