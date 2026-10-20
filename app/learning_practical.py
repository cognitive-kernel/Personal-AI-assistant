"""Deterministic practical exercises for code-capable lessons.

Exercise grading runs in the existing consent-gated Docker sandbox. The learner
program runs as a separate child process for each case; the grading wrapper
records only case outcomes, never expected values or learner output.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .db import learning_connection
from .learning import MASTERY_SCORE_THRESHOLD

EXERCISE_ID = "python-json-sum-v1"
EXERCISES = {
    EXERCISE_ID: {
        "id": EXERCISE_ID,
        "title": "جمع مقادیر JSON با پایتون",
        "description": (
            "برنامه‌ای بنویسید که یک شیء JSON را از ورودی استاندارد بخواند. "
            "شیء ورودی دارای آرایه values از اعداد است؛ خروجی باید یک عدد JSON برابر مجموع مقادیر باشد."
        ),
        "input_example": {"values": [2, 3]},
        "output_example": 5,
        "language": "python",
        "topic_keywords": ("python", "پایتون"),
        "test_count": 4,
        "grading_version": "python-json-sum-grader-v1",
    }
}

# These cases are intentionally not returned by the catalog endpoint.
_TEST_CASES = {
    EXERCISE_ID: [
        {"id": "positive-integers", "input": {"values": [1, 2, 3]}, "expected": 6},
        {"id": "empty-list", "input": {"values": []}, "expected": 0},
        {"id": "negative-and-fractional", "input": {"values": [-2, 1.5, 3]}, "expected": 2.5},
        {"id": "large-integers", "input": {"values": [1000000000000, 1]}, "expected": 1000000000001},
    ]
}


def list_exercises() -> list[dict[str, Any]]:
    """Return public exercise instructions without hidden grading cases."""
    return [
        {key: value for key, value in item.items() if key != "topic_keywords"}
        for item in EXERCISES.values()
    ]


def validate_practical_job(job_id: int, exercise_id: str) -> dict[str, Any]:
    """Ensure a completed, relevant lesson exists before consuming sandbox resources."""
    exercise = EXERCISES.get(exercise_id)
    if exercise is None:
        raise KeyError(exercise_id)
    with learning_connection() as conn:
        job = conn.execute(
            """SELECT j.id, j.status, j.topic, j.path_topic_id, p.curriculum_slug
               FROM learning_jobs AS j
               LEFT JOIN learning_path_topics AS t ON t.id=j.path_topic_id
               LEFT JOIN learning_paths AS p ON p.id=t.path_id
               WHERE j.id=?""",
            (job_id,),
        ).fetchone()
    if job is None:
        raise KeyError(job_id)
    if job["status"] != "completed":
        raise ValueError("Practical assessment requires a completed lesson.")
    topic = str(job["topic"]).casefold()
    curriculum_slug = str(job["curriculum_slug"] or "").casefold()
    if not any(keyword in topic for keyword in exercise["topic_keywords"]) and curriculum_slug != "python":
        raise ValueError("This practical exercise does not match the lesson topic.")
    return {"id": int(job["id"]), "topic": str(job["topic"]), "path_topic_id": job["path_topic_id"], "curriculum_slug": curriculum_slug}


def get_test_cases(exercise_id: str) -> list[dict[str, Any]]:
    """Return the server-side grading cases to the trusted task handler only."""
    if exercise_id not in _TEST_CASES:
        raise KeyError(exercise_id)
    return [
        {"id": item["id"], "input": item["input"], "expected": item["expected"]}
        for item in _TEST_CASES[exercise_id]
    ]


def grade_case_output(exercise_id: str, case_id: str, status: str, exit_code: int | None, stdout: str) -> bool:
    """Grade one child-process output in the trusted host process."""
    cases = _TEST_CASES.get(exercise_id)
    case = next((item for item in cases or [] if item["id"] == case_id), None)
    if case is None or status != "succeeded" or exit_code != 0 or not isinstance(stdout, str):
        return False
    if len(stdout.encode("utf-8", errors="replace")) > 4096:
        return False
    try:
        actual = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    expected = case["expected"]
    if (
        isinstance(actual, (int, float)) and not isinstance(actual, bool)
        and isinstance(expected, (int, float)) and not isinstance(expected, bool)
    ):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    return type(actual) is type(expected) and actual == expected


def build_grade_report(exercise_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a validated, output-free grading report from server-side case outcomes."""
    exercise = EXERCISES.get(exercise_id)
    if exercise is None:
        raise KeyError(exercise_id)
    expected_ids = {item["id"] for item in _TEST_CASES[exercise_id]}
    if (
        not isinstance(results, list) or len(results) != len(expected_ids)
        or any(not isinstance(item, dict) or set(item) != {"case_id", "passed"}
               or not isinstance(item.get("case_id"), str) or not isinstance(item.get("passed"), bool)
               for item in results)
        or {item["case_id"] for item in results} != expected_ids
    ):
        raise ValueError("Practical case results failed validation.")
    return {
        "schema_version": 1,
        "exercise_id": exercise_id,
        "grading_version": exercise["grading_version"],
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def record_practical_attempt(job_id: int, exercise_id: str, report: dict[str, Any], source_code: str) -> dict[str, Any]:
    """Persist a completed sandbox grade and apply mastery only after prerequisites pass."""
    exercise = EXERCISES.get(exercise_id)
    if exercise is None:
        raise KeyError(exercise_id)
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("exercise_id") != exercise_id:
        raise ValueError("Practical grading report is invalid.")
    results = report.get("results")
    total = report.get("total")
    passed = report.get("passed")
    expected_ids = {item["id"] for item in _TEST_CASES[exercise_id]}
    if (
        not isinstance(results, list) or not isinstance(total, int) or isinstance(total, bool)
        or not isinstance(passed, int) or isinstance(passed, bool)
        or total != len(expected_ids) or len(results) != total or not 0 <= passed <= total
        or {item.get("case_id") for item in results if isinstance(item, dict)} != expected_ids
        or any(not isinstance(item, dict) or not isinstance(item.get("passed"), bool) for item in results)
        or sum(item["passed"] for item in results) != passed
        or report.get("grading_version") != exercise["grading_version"]
    ):
        raise ValueError("Practical grading report failed validation.")

    score = round(100 * passed / total, 2)
    source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
    with learning_connection() as conn:
        job = conn.execute(
            """SELECT j.status, j.topic, j.path_topic_id, p.curriculum_slug
               FROM learning_jobs AS j
               LEFT JOIN learning_path_topics AS t ON t.id=j.path_topic_id
               LEFT JOIN learning_paths AS p ON p.id=t.path_id
               WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        if job is None:
            raise KeyError(job_id)
        if job["status"] != "completed":
            raise ValueError("Practical assessment requires a completed lesson.")
        topic = str(job["topic"]).casefold()
        curriculum_slug = str(job["curriculum_slug"] or "").casefold()
        if not any(keyword in topic for keyword in exercise["topic_keywords"]) and curriculum_slug != "python":
            raise ValueError("This practical exercise does not match the lesson topic.")
        path_topic_id = job["path_topic_id"]
        blockers = 0
        if path_topic_id is not None:
            blockers = conn.execute(
                """SELECT COUNT(*) AS count FROM learning_path_topics AS prior
                   JOIN learning_path_topics AS current ON current.path_id=prior.path_id
                   WHERE current.id=? AND prior.position < current.position AND prior.status != 'completed'""",
                (int(path_topic_id),),
            ).fetchone()["count"]
        cursor = conn.execute(
            """INSERT INTO assessment_attempts(job_id, answers_json, score, correct_count, question_count, scoring_method)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                json.dumps({
                    "exercise_id": exercise_id,
                    "grading_version": exercise["grading_version"],
                    "source_sha256": source_hash,
                    "results": results,
                }, ensure_ascii=False),
                score, passed, total, "sandbox_practical_v1",
            ),
        )
        attempt_id = int(cursor.lastrowid)
        mastery = path_topic_id is not None and score >= MASTERY_SCORE_THRESHOLD and int(blockers) == 0
        if path_topic_id is not None:
            conn.execute(
                """UPDATE learning_path_topics SET latest_score=?,
                   status=CASE WHEN status='completed' THEN status WHEN ? THEN 'completed' ELSE 'in_progress' END,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (score, int(mastery), int(path_topic_id)),
            )
    return {
        "attempt_id": attempt_id,
        "job_id": job_id,
        "exercise_id": exercise_id,
        "score": score,
        "passed_tests": passed,
        "total_tests": total,
        "scoring_method": "sandbox_practical_v1",
        "mastery_achieved": mastery,
        "mastery_threshold": MASTERY_SCORE_THRESHOLD,
        "prerequisites_met": int(blockers) == 0,
        "source_sha256": source_hash,
        "independent_execution": True,
        "independent_scientific_verification": False,
        "note": "امتیاز از اجرای کد در sandbox و عبور آزمون‌های ورودی/خروجی نسخه‌بندی‌شده به دست آمده است؛ این نتیجه فقط مهارت همین تمرین را می‌سنجد.",
    }
