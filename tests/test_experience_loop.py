from app import db, experience
from app.experience_loop import verify_learning_artifact


def setup_learning_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()


def _assessment():
    return {
        "learning_outcomes": ["Explain the mechanism", "Compare two approaches", "Measure the result"],
        "questions": ["What causes the behavior?", "What is the trade-off?", "How is the result measured?"],
        "answer_key": ["The documented mechanism determines the behavior.", "The trade-off depends on workload.", "Measure the baseline and compare results."],
    }


def test_controlled_loop_verifies_only_when_quality_contract_and_source_integrity_pass(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    excerpt = (
        "The Python documentation describes JSON decoding behavior. "
        "Applications should handle malformed input and report decoding errors. "
        "This excerpt is retained verbatim for provenance and must not be treated as proof of every lesson claim."
    )
    sources = [{
        "url": "https://docs.python.org/3/library/json.html",
        "title": "JSON — Python documentation",
        "content": excerpt,
    }]
    result = verify_learning_artifact(
        topic="Python JSON decoding",
        lesson=("JSON decoding converts serialized text into Python values. " * 80) + "\\nSources: https://docs.python.org/3/library/json.html",
        assessment=_assessment(),
        quality={"accepted": True, "word_count": 160, "target_word_count": 120, "score": 90},
        retrieved_sources=sources,
        job_id=17,
    )
    assert result["status"] == "verified"
    assert all(check["passed"] for check in result["checks"])
    saved = experience.get_experience(result["experience_id"])
    assert saved["status"] == "verified"
    assert saved["test_results"][0]["runner"] == "experience-loop-v1"
    assert saved["source_evidence"][0]["excerpt"] == excerpt[:400]
    assert "not independently establish factual correctness" in saved["takeaway"]


def test_controlled_loop_keeps_artifact_unverified_without_real_source_excerpt(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    result = verify_learning_artifact(
        topic="General explanation",
        lesson=("A sufficiently detailed lesson describes mechanisms, constraints, and examples. " * 40),
        assessment=_assessment(),
        quality={"accepted": True, "word_count": 140, "target_word_count": 120, "score": 80},
        retrieved_sources=[],
        job_id=18,
    )
    assert result["status"] == "needs_review"
    assert result["checks"][-1]["passed"] is False
    assert experience.get_experience(result["experience_id"])["status"] == "needs_review"


def test_controlled_loop_records_failed_quality_gate(tmp_path, monkeypatch):
    setup_learning_db(tmp_path, monkeypatch)
    excerpt = "Official documentation source excerpt retained verbatim. " * 5
    result = verify_learning_artifact(
        topic="Python JSON decoding",
        lesson="Too short",
        assessment=_assessment(),
        quality={"accepted": False, "word_count": 2, "target_word_count": 120, "score": 5},
        retrieved_sources=[{
            "url": "https://docs.python.org/3/library/json.html",
            "title": "JSON docs",
            "content": excerpt,
        }],
        job_id=19,
    )
    assert result["status"] == "needs_review"
    assert result["checks"][0]["passed"] is False



def test_static_code_checks_fail_on_invalid_python_and_json_without_execution():
    from app.experience_loop import static_code_checks

    fence = chr(96) * 3
    checks = static_code_checks(
        f"Bad Python:\n{fence}python\nprint(\n{fence}\nBad JSON:\n{fence}json\n{{\"key\": }}\n{fence}"
    )
    assert len(checks) == 2
    assert all(check["passed"] is False for check in checks)
    assert {check["test"] for check in checks} == {"python_syntax_block_1", "json_syntax_block_1"}


def test_static_code_checks_pass_valid_examples_and_report_no_code_case():
    from app.experience_loop import static_code_checks

    fence = chr(96) * 3
    valid = static_code_checks(f'{fence}python\nprint("ok")\n{fence}\n{fence}json\n{{"ok": true}}\n{fence}')
    assert len(valid) == 2
    assert all(check["passed"] for check in valid)
    absent = static_code_checks("An explanation without executable code.")
    assert absent == [{
        "test": "code_syntax_contract",
        "passed": True,
        "actual": "No fenced Python or JSON blocks were present; no code execution was attempted.",
    }]
