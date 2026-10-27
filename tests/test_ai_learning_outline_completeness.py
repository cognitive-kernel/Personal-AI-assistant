"""Structural completion checks for ChatGPT-generated outlines.

These tests intentionally do not judge factual correctness; that belongs to a
separate user-review phase.
"""
import pytest

from app.ai_learning import service
from app.ai_learning.db import initialize_store
from app.ai_learning.service import _outline_completion_error


def _outline(numbers=(1, 2, 3), audit_status="COMPLETE", total=3, missing="none"):
    body = "\n".join(f"{number}. Topic {number}" for number in numbers)
    return (
        body
        + f"\nAUDIT: status={audit_status}; total_topics={total}; "
        + f"missing_domains={missing}; end_reason=all_applicable_domains_covered"
    )


def test_complete_outline_requires_matching_audit_and_contiguous_numbering():
    text = _outline()
    assert _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"]) is None


def test_missing_audit_does_not_claim_outline_complete():
    text = "1. Topic 1\n2. Topic 2\n3. Topic 3"
    error = _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"])
    assert error and "AUDIT" in error


def test_continue_required_audit_keeps_outline_in_recovery_state():
    text = _outline(audit_status="CONTINUE_REQUIRED")
    error = _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"])
    assert error and "تکمیل" in error


def test_mismatched_declared_topic_count_is_rejected():
    text = _outline(total=4)
    error = _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"])
    assert error and "تعداد" in error


def test_numbering_gap_is_rejected():
    text = _outline(numbers=(1, 3, 4), total=3)
    error = _outline_completion_error(text, ["Topic 1", "Topic 3", "Topic 4"])
    assert error and "شماره‌گذاری" in error


def test_missing_domains_are_not_marked_complete():
    text = _outline(missing="security, troubleshooting")
    error = _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"])
    assert error and "حوزه" in error


def test_duplicate_audit_lines_are_rejected():
    text = _outline() + "\\nAUDIT: status=COMPLETE; total_topics=3; missing_domains=none; end_reason=duplicate"
    error = _outline_completion_error(text, ["Topic 1", "Topic 2", "Topic 3"])
    assert error and "AUDIT" in error


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()


def test_raw_outline_is_preserved_if_semantic_duplicate_check_fails(isolated_store, monkeypatch):
    course = service.create_course(title="Outline recovery test")
    service.add_topic(course["id"], "Existing topic")
    run = service.create_run(
        course["id"], run_type="outline", request_key="outline-dedup-failure-test"
    )
    monkeypatch.setattr(
        service, "_filter_duplicate_titles",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("embedding unavailable")),
    )
    response = (
        "1. New topic\n"
        "AUDIT: status=COMPLETE; total_topics=1; "
        "missing_domains=none; end_reason=all_applicable_domains_covered"
    )

    result = service.complete_run(run["id"], response)

    assert result["status"] == "needs_user_action"
    assert result["error_code"] == "outline_duplicate_check_unavailable"
    assert result["response_text"] == response
    assert len(service.get_course(course["id"])["topics"]) == 1
