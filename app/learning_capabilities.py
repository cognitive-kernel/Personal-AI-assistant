"""Read-only capability map derived from persisted learning assessments.

The map reports evidence strength, not a claim of semantic mastery. Exact-match
assessment scores remain provisional until repeatable practical tasks are added.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .db import init_databases, learning_connection
from . import learning_cost_manager

_MAX_LIMIT = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _practical_evidence_map(connection, topics: set[str]) -> dict[str, dict[str, Any]]:
    """Count distinct successful sandbox exercises with live source provenance."""
    result_map: dict[str, dict[str, Any]] = {}
    if not topics:
        return result_map
    rows = connection.execute(
        """SELECT topic, test_results_json FROM learning_experiences
           WHERE status='verified' ORDER BY id DESC LIMIT 5000"""
    ).fetchall()
    valid_by_topic: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for row in rows:
        topic = str(row["topic"]).strip().casefold()
        if topic not in topics:
            continue
        try:
            results = json.loads(row["test_results_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict) or item.get("passed") is not True or item.get("runner") != "docker-sandbox":
                continue
            test_name, source_hash = item.get("test"), item.get("source_sha256")
            if not isinstance(test_name, str) or not test_name.strip() or not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
                continue
            sources = item.get("source_evidence")
            if not isinstance(sources, list) or not sources:
                continue
            if not all(
                isinstance(source, dict)
                and isinstance(source.get("url"), str)
                and source["url"].startswith("https://")
                and isinstance(source.get("live_content_sha256"), str)
                and _SHA256.fullmatch(source["live_content_sha256"])
                and isinstance(source.get("excerpt_sha256"), str)
                and _SHA256.fullmatch(source["excerpt_sha256"])
                for source in sources
            ):
                continue
            valid_by_topic.setdefault(topic, {})[(test_name.strip(), source_hash)] = item
    for topic in topics:
        valid = valid_by_topic.get(topic.casefold(), {})
        hashes = {source_hash for _, source_hash in valid}
        names = {name for name, _ in valid}
        result_map[topic] = {
            "practical_attempt_count": len(valid),
            "practical_test_names": sorted(names)[:20],
            "practical_source_count": len(hashes),
            "practical_repeatable": len(hashes) >= 2 and len(names) >= 2,
        }
    return result_map



def _transfer_evidence_map(connection, topics: set[str]) -> dict[str, dict[str, Any]]:
    """Count repeatable transfer cases only when they descend from retained knowledge."""
    if not topics:
        return {}
    rows = connection.execute(
        """SELECT e.topic, e.problem, e.originating_knowledge_id, e.test_results_json,
                  k.topic AS knowledge_topic, k.status AS knowledge_status
           FROM learning_experiences e
           JOIN knowledge_items k ON k.id=e.originating_knowledge_id
           WHERE e.status='verified' AND k.status='retained_after_review'
           ORDER BY e.id DESC LIMIT 5000"""
    ).fetchall()
    by_topic: dict[str, dict[int, set[tuple[str, str, str]]]] = {}
    # Exact hashes alone treat trivial paraphrases as separate transfer cases.
    # Canonicalize highly overlapping token sets so near-duplicate problem statements
    # cannot inflate the provisional transfer signal.
    scenario_tokens: dict[str, dict[int, dict[str, set[str]]]] = {}
    for row in rows:
        topic = str(row["knowledge_topic"]).strip().casefold()
        knowledge_id = row["originating_knowledge_id"]
        problem = row["problem"]
        if topic not in topics or not isinstance(knowledge_id, int) or not isinstance(problem, str) or len(problem.strip()) < 20:
            continue
        try:
            results = json.loads(row["test_results_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(results, list):
            continue
        normalized_problem = re.sub(r"\s+", " ", problem).strip().casefold()
        scenario_hash = hashlib.sha256(normalized_problem.encode("utf-8")).hexdigest()
        tokens = set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalized_problem))
        if len(tokens) < 4:
            continue
        known_scenarios = scenario_tokens.setdefault(topic, {}).setdefault(int(knowledge_id), {})
        canonical_scenario_hash = scenario_hash
        scenario_already_known = False
        for existing_hash, existing_tokens in known_scenarios.items():
            union = tokens | existing_tokens
            similarity = (len(tokens & existing_tokens) / len(union)) if union else 1.0
            if similarity >= 0.80:
                canonical_scenario_hash = existing_hash
                scenario_already_known = True
                break
        scenario_registered = False
        for item in results:
            if not isinstance(item, dict) or item.get("passed") is not True or item.get("runner") != "docker-sandbox":
                continue
            test_name, source_hash = item.get("test"), item.get("source_sha256")
            sources = item.get("source_evidence")
            if not isinstance(test_name, str) or not test_name.strip() or not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
                continue
            if not isinstance(sources, list) or not sources or not all(
                isinstance(source, dict)
                and isinstance(source.get("url"), str)
                and source["url"].startswith("https://")
                and isinstance(source.get("live_content_sha256"), str)
                and _SHA256.fullmatch(source["live_content_sha256"])
                and isinstance(source.get("excerpt_sha256"), str)
                and _SHA256.fullmatch(source["excerpt_sha256"])
                for source in sources
            ):
                continue
            # Only evidence-backed, passing runs may reserve a scenario identity.
            # An unprovenanced experience must not suppress a later valid case.
            if not scenario_registered:
                if not scenario_already_known:
                    known_scenarios[canonical_scenario_hash] = tokens
                scenario_registered = True
            by_topic.setdefault(topic, {}).setdefault(int(knowledge_id), set()).add(
                (test_name.strip(), source_hash, canonical_scenario_hash)
            )
    result: dict[str, dict[str, Any]] = {}
    for topic in topics:
        per_knowledge = by_topic.get(topic.casefold(), {})
        candidates = []
        for knowledge_id, attempts in per_knowledge.items():
            scenario_hashes = {scenario for _name, _source, scenario in attempts}
            test_names = {name for name, _source, _scenario in attempts}
            source_hashes = {source for _name, source, _scenario in attempts}
            repeatable = len(scenario_hashes) >= 2 and len(test_names) >= 2 and len(source_hashes) >= 2
            candidates.append((repeatable, len(scenario_hashes), len(attempts), -knowledge_id, knowledge_id, attempts))
        if candidates:
            repeatable, _scenario_count, _attempt_count, _negative_id, best_knowledge_id, best = max(candidates)
        else:
            repeatable, best_knowledge_id, best = False, None, set()
        scenario_hashes = {scenario for _name, _source, scenario in best}
        result[topic] = {
            "transfer_case_count": len(scenario_hashes),
            "transfer_attempt_count": len(best),
            "transfer_repeatable": bool(repeatable),
            "transfer_evidence_knowledge_ids": [
                knowledge_id for knowledge_id, attempts in per_knowledge.items()
                if len({scenario for _name, _source, scenario in attempts}) >= 2
                and len({name for name, _source, _scenario in attempts}) >= 2
                and len({source for _name, source, _scenario in attempts}) >= 2
            ][:10],
        }
    return result


def get_capability_map(*, path_id: int | None = None, limit: int = 200) -> dict[str, Any]:
    if path_id is not None and (
        isinstance(path_id, bool) or not isinstance(path_id, int) or path_id < 1
    ):
        raise ValueError("path_id must be a positive integer when provided.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {_MAX_LIMIT}.")
    init_databases()
    with learning_connection() as connection:
        if path_id is not None and connection.execute(
            "SELECT 1 FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone() is None:
            raise KeyError(path_id)
        sql = """SELECT p.id AS path_id, p.title AS path_title, p.curriculum_slug,
                        t.id AS topic_id, t.topic, t.position, t.status AS topic_status,
                        t.latest_score AS persisted_topic_score, t.updated_at AS topic_updated_at,
                        (SELECT COUNT(*)
                         FROM assessment_attempts a
                         JOIN learning_jobs j ON j.id=a.job_id
                         WHERE j.path_topic_id=t.id
                           AND a.scoring_method='normalized_exact_match'
                           AND a.score BETWEEN 0 AND 100
                           AND a.question_count > 0
                           AND a.correct_count BETWEEN 0 AND a.question_count) AS assessment_count,
                        latest.id AS latest_attempt_id,
                        latest.score AS latest_attempt_score,
                        latest.correct_count AS latest_correct_count,
                        latest.question_count AS latest_question_count,
                        latest.scoring_method AS latest_scoring_method,
                        latest.created_at AS latest_assessed_at
                 FROM learning_path_topics t
                 JOIN learning_paths p ON p.id=t.path_id
                 LEFT JOIN assessment_attempts latest ON latest.id=(
                    SELECT a.id
                    FROM assessment_attempts a
                    JOIN learning_jobs j ON j.id=a.job_id
                    WHERE j.path_topic_id=t.id
                    ORDER BY a.id DESC LIMIT 1
                 )"""
        params: list[Any] = []
        if path_id is not None:
            sql += " WHERE p.id=?"
            params.append(path_id)
        sql += " ORDER BY p.id, t.position, t.id LIMIT ?"
        params.append(limit)
        rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
        topic_keys = {str(row["topic"]).strip().casefold() for row in rows}
        practical_by_topic = _practical_evidence_map(connection, topic_keys)
        transfer_by_topic = _transfer_evidence_map(connection, topic_keys)
        topic_ids = [int(row["topic_id"]) for row in rows]
        usage_by_topic: dict[int, dict[str, Any]] = {}
        if topic_ids:
            placeholders = ",".join("?" for _ in topic_ids)
            usage_rows = connection.execute(
                f"""SELECT j.path_topic_id AS topic_id, COUNT(u.id) AS model_call_count,
                           SUM(u.prompt_tokens) AS prompt_tokens,
                           SUM(u.completion_tokens) AS completion_tokens,
                           SUM(u.total_duration_ns) AS total_duration_ns,
                           SUM(u.estimated_cost_microusd) AS estimated_cost_microusd,
                           COUNT(u.estimated_cost_microusd) AS cost_estimate_count,
                           GROUP_CONCAT(DISTINCT u.model_name) AS models
                    FROM model_usage_events u
                    JOIN learning_jobs j ON j.task_id=u.task_id
                    WHERE j.path_topic_id IN ({placeholders})
                    GROUP BY j.path_topic_id""",
                topic_ids,
            ).fetchall()
            usage_by_topic = {
                int(item["topic_id"]): {
                    "model_call_count": int(item["model_call_count"] or 0),
                    "prompt_tokens": int(item["prompt_tokens"] or 0),
                    "completion_tokens": int(item["completion_tokens"] or 0),
                    "inference_duration_ms": round(int(item["total_duration_ns"] or 0) / 1_000_000, 2),
                    "models_used": sorted(set(str(item["models"] or "").split(",")) - {""}),
                    "estimated_cost_microusd": int(item["estimated_cost_microusd"]) if item["estimated_cost_microusd"] is not None else None,
                    "cost_estimate_count": int(item["cost_estimate_count"] or 0),
                }
                for item in usage_rows
            }

        score_history: dict[int, list[float]] = {}
        if topic_ids:
            score_rows = connection.execute(
                f"""SELECT j.path_topic_id AS topic_id, a.score
                    FROM assessment_attempts a
                    JOIN learning_jobs j ON j.id=a.job_id
                    WHERE j.path_topic_id IN ({placeholders})
                      AND a.score BETWEEN 0 AND 100
                      AND a.question_count > 0
                    ORDER BY a.id DESC""",
                topic_ids,
            ).fetchall()
            for score_row in score_rows:
                values = score_history.setdefault(int(score_row["topic_id"]), [])
                if len(values) < 2:
                    values.append(float(score_row["score"]))

    capabilities: list[dict[str, Any]] = []
    for row in rows:
        score = row["latest_attempt_score"]
        correct = row["latest_correct_count"]
        questions = row["latest_question_count"]
        method = row["latest_scoring_method"]
        count = int(row["assessment_count"] or 0)
        valid = (
            row["latest_attempt_id"] is not None
            and method == "normalized_exact_match"
            and isinstance(score, (int, float))
            and 0 <= float(score) <= 100
            and isinstance(correct, int)
            and isinstance(questions, int)
            and questions > 0
            and 0 <= correct <= questions
        )
        if row["latest_attempt_id"] is None:
            status = "not_assessed"
            confidence = "none"
        elif not valid:
            status = "invalid_evidence"
            confidence = "none"
            score = None
        elif float(score) < 80:
            status = "needs_reinforcement"
            confidence = "provisional"
        elif count >= 2:
            status = "repeated_provisional"
            confidence = "provisional_repeat"
        else:
            status = "provisional"
            confidence = "provisional"

        practical = practical_by_topic.get(str(row["topic"]).strip().casefold(), {
            "practical_attempt_count": 0, "practical_test_names": [],
            "practical_source_count": 0, "practical_repeatable": False,
        })
        transfer = transfer_by_topic.get(str(row["topic"]).strip().casefold(), {
            "transfer_case_count": 0, "transfer_attempt_count": 0,
            "transfer_repeatable": False, "transfer_evidence_knowledge_ids": [],
        })
        if transfer["transfer_repeatable"]:
            if valid and float(score) < 80:
                status = "conflicting_evidence"
                confidence = "conflicting"
            else:
                status = "transfer_provisional"
                confidence = "provisional_transfer"

        if practical["practical_repeatable"] and not transfer["transfer_repeatable"]:
            if valid and float(score) < 80:
                status = "conflicting_evidence"
                confidence = "conflicting"
            else:
                status = "practical_provisional"
                confidence = "provisional_practical"

        usage = usage_by_topic.get(int(row["topic_id"]), {
            "model_call_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "inference_duration_ms": 0.0, "models_used": [],
            "estimated_cost_microusd": None, "cost_estimate_count": 0,
        })
        if usage["cost_estimate_count"] == 0:
            usage["cost_status"] = "unknown_rate"
        elif usage["cost_estimate_count"] < usage["model_call_count"]:
            usage["cost_status"] = "partial_estimate"
        else:
            usage["cost_status"] = "configured_estimate"
        value_metrics = learning_cost_manager.learning_value_metrics(
            prior_score=(score_history.get(int(row["topic_id"]), [None, None])[1]),
            current_score=(score_history.get(int(row["topic_id"]), [float(score) if valid else None])[0]),
            estimated_cost_microusd=usage["estimated_cost_microusd"],
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            practical_successes=practical["practical_attempt_count"],
            independent_source_count=practical["practical_source_count"],
        )
        capabilities.append({
            "path_id": int(row["path_id"]),
            "path_title": row["path_title"],
            "curriculum_slug": row["curriculum_slug"],
            "topic_id": int(row["topic_id"]),
            "capability": row["topic"],
            "topic_position": int(row["position"]),
            "topic_status": row["topic_status"],
            "status": status,
            "latest_score": float(score) if valid else None,
            "assessment_count": count,
            **practical,
            **transfer,
            **usage,
            "learning_value_cost": value_metrics,
            "score_points_per_1000_output_tokens": (
                round(float(score) * 1000 / usage["completion_tokens"], 3)
                if valid and usage["completion_tokens"] > 0 else None
            ),
            "latest_attempt_id": row["latest_attempt_id"],
            "correct_count": int(correct) if valid else None,
            "question_count": int(questions) if valid else None,
            "scoring_method": method if valid else None,
            "last_assessed_at": row["latest_assessed_at"] if valid else None,
            "confidence": confidence,
            "evidence_limit": (
                "Transfer evidence requires distinct problem statements and passing sandbox tests tied to the same retained knowledge item with live source provenance; it remains provisional and does not prove general transfer or semantic mastery."
                if transfer["transfer_repeatable"] else
                "Repeated practical evidence means distinct passing sandbox test programs with current-page source excerpts; it remains provisional and does not prove general transfer or semantic mastery."
                if practical["practical_repeatable"] else
                "Exact-match assessments are provisional evidence; semantic mastery and practical transfer are not established."
            ),
        })

    return {
        "count": len(capabilities),
        "path_id": path_id,
        "capabilities": capabilities,
        "summary": {
            "not_assessed": sum(item["status"] == "not_assessed" for item in capabilities),
            "invalid_evidence": sum(item["status"] == "invalid_evidence" for item in capabilities),
            "needs_reinforcement": sum(item["status"] == "needs_reinforcement" for item in capabilities),
            "provisional": sum(item["status"] == "provisional" for item in capabilities),
            "repeated_provisional": sum(item["status"] == "repeated_provisional" for item in capabilities),
            "practical_provisional": sum(item["status"] == "practical_provisional" for item in capabilities),
            "transfer_provisional": sum(item["status"] == "transfer_provisional" for item in capabilities),
            "conflicting_evidence": sum(item["status"] == "conflicting_evidence" for item in capabilities),
        },
    }
