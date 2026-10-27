"""Structural completion checks for ChatGPT-generated outlines.

These tests intentionally do not judge factual correctness; that belongs to a
separate user-review phase.
"""
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
