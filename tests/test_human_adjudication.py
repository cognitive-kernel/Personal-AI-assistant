import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "create_human_adjudication.py"
SPEC = importlib.util.spec_from_file_location("human_adjudication", SCRIPT)
worksheet_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(worksheet_module)


def _dataset(path):
    cases = [
        {
            "id": "en-case", "language": "en", "prompt": "What is a database index?",
            "expected_concepts": ["lookup performance"], "evidence_policy": "Use reliable sources.",
            "must_not_claim": ["indexes are always free"],
        },
        {
            "id": "fa-case", "language": "fa", "prompt": "شاخص پایگاه داده چیست؟",
            "expected_concepts": ["سرعت جست‌وجو"], "evidence_policy": "از منبع معتبر استفاده کن.",
            "must_not_claim": ["هزینهٔ نگهداری ندارد"],
        },
    ]
    path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    return cases


def _report(dataset, cases):
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    return {
        "report_schema_version": 1,
        "dataset_sha256": digest,
        "created_at_utc": "2026-10-16T00:00:00Z",
        "code_commit": "a" * 40,
        "model": {"configured_name": "local-model", "manifest_digest": "sha256:" + "b" * 64},
        "run_status": "completed",
        "case_count": len(cases),
        "completed_count": len(cases),
        "failed_count": 0,
        "results": [
            {
                "case_id": case["id"], "language": case["language"],
                "generation_status": "completed", "answer": "answer",
                "source_urls": ["https://example.invalid/source"],
                "source_count": 1, "surface_matched_concepts": [],
                "surface_missing_concepts": case["expected_concepts"],
                "prohibited_phrase_hits": [], "expected_language_script_present": True,
            }
            for case in cases
        ],
        "source_snapshots": [
            {"case_id": case["id"], "sources": [{"url": "https://example.invalid/source", "title": "Example", "content_sha256": "c" * 64}]}
            for case in cases
        ],
    }


def test_build_human_worksheet_binds_exact_dataset_and_leaves_ratings_unset(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    cases = _dataset(dataset)
    worksheet = worksheet_module.build_worksheet(_report(dataset, cases), dataset_path=dataset)

    assert worksheet["worksheet_schema_version"] == 1
    assert worksheet["rubric_version"] == "v1"
    assert worksheet["created_for_report"]["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert len(worksheet["cases"]) == 2
    assert {case["language"] for case in worksheet["cases"]} == {"fa", "en"}
    assert all(case["answer_available"] for case in worksheet["cases"])
    assert all(case["adjudication"]["factual_correctness"] is None for case in worksheet["cases"])
    assert "score" not in worksheet
    assert worksheet["rubric_fields"]["source_support"]


def test_human_worksheet_rejects_dataset_hash_mismatch(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    cases = _dataset(dataset)
    report = _report(dataset, cases)
    report["dataset_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="hash"):
        worksheet_module.build_worksheet(report, dataset_path=dataset)


def test_human_worksheet_rejects_duplicate_and_unknown_result_ids(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    cases = _dataset(dataset)
    report = _report(dataset, cases)
    report["results"][1]["case_id"] = report["results"][0]["case_id"]

    with pytest.raises(ValueError, match="duplicate or unknown"):
        worksheet_module.build_worksheet(report, dataset_path=dataset)


def test_cli_outputs_only_summary_and_creates_worksheet(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    cases = _dataset(dataset)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report(dataset, cases)), encoding="utf-8")
    output = tmp_path / "review.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(report_path),
         "--dataset", str(dataset), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0
    assert json.loads(output.read_text(encoding="utf-8"))["interpretation"].startswith("This is an unscored worksheet")
    summary = json.loads(result.stdout)
    assert summary["case_count"] == 2
    assert summary["scored"] is False
    assert str(tmp_path) not in result.stdout
