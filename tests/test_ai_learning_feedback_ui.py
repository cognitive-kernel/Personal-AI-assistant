from pathlib import Path


def test_ai_learning_search_exposes_trace_bound_feedback_controls():
    root = Path(__file__).resolve().parents[1] / "app" / "ai_learning" / "static"
    script = (root / "app.js").read_text(encoding="utf-8")
    assert '"/feedback"' in script
    assert "query_trace_id:r.trace_id" in script
    assert "submitFeedback(1)" in script
    assert "submitFeedback(-1)" in script
    assert "مفید بود" in script
    assert "مفید نبود" in script
