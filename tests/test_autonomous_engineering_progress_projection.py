from app.autonomous_engineering_progress_projection import enrich_progress_payload


def test_progress_projection_reports_active_codex_step_without_overwriting_phase():
    current = {"run_id": "123", "status": "awaiting_review", "phase": "packaging", "percent": 82}
    jobs = [{
        "name": "codex",
        "status": "in_progress",
        "steps": [
            {"name": "Checkout", "status": "completed"},
            {"name": "Run tests", "status": "in_progress"},
        ],
    }]

    result = enrich_progress_payload(current, jobs)

    assert result["status"] == "running"
    assert result["activity"] == "Run tests"
    assert result["phase"] == "packaging"
    assert result["percent"] == 82
    assert current["status"] == "awaiting_review"


def test_progress_projection_reports_failure_and_queued_codex():
    failed = enrich_progress_payload(
        {"status": "running"},
        [{"name": "Codex", "status": "completed", "conclusion": "failure"}],
    )
    queued = enrich_progress_payload(
        {"status": "running"},
        [{"name": "codex", "status": "queued", "conclusion": None}],
    )

    assert failed["status"] == "failed"
    assert "failure" in failed["activity"]
    assert queued["status"] == "queued"
    assert queued["phase"] == "queued"


def test_progress_projection_marks_only_fully_successful_jobs_complete():
    completed = enrich_progress_payload(
        {"status": "running", "percent": 95},
        [
            {"name": "codex", "status": "completed", "conclusion": "success"},
            {"name": "gate", "status": "completed", "conclusion": "skipped"},
        ],
    )
    unsuccessful = enrich_progress_payload(
        {"status": "running", "percent": 95},
        [{"name": "codex", "status": "completed", "conclusion": "failure"}],
    )

    assert completed["status"] == "completed"
    assert completed["percent"] == 100
    assert unsuccessful["status"] == "failed"
