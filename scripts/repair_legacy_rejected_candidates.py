from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db


def repair_legacy_rejected_candidates(*, apply: bool = False) -> dict[str, Any]:
    """Recover only hash-verifiable fields from legacy scrubbed rejection records.

    The default mode is read-only. This utility never deletes data and never
    reconstructs source text, assessments, test plans, or knowledge versions
    that the old rejection path already erased.
    """
    db.init_databases()
    with db.learning_connection() as connection:
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """SELECT c.id, c.job_id, c.content, c.content_sha256,
                      c.source_urls_json, c.quality_report_json, c.test_plan_json,
                      c.failure_reason, c.promoted_knowledge_id,
                      j.lesson AS job_lesson, j.quality_json AS job_quality_json,
                      j.assessment_json AS job_assessment_json,
                      EXISTS(
                          SELECT 1 FROM learning_source_text st WHERE st.job_id=c.job_id
                      ) AS source_text_exists,
                      EXISTS(
                          SELECT 1 FROM knowledge_items k WHERE k.job_id=c.job_id
                      ) AS knowledge_item_exists
               FROM temporary_knowledge_candidates c
               JOIN learning_jobs j ON j.id=c.job_id
               WHERE c.status='failed'
                 AND c.failure_reason LIKE 'rejected_after_review:%'
                 AND c.failure_reason NOT LIKE '%legacy_recovery:%'
                 AND c.promoted_knowledge_id IS NULL
                 AND (c.content='' OR c.source_urls_json='[]' OR c.quality_report_json='{}')
               ORDER BY c.id"""
        ).fetchall()

        reports: list[dict[str, Any]] = []
        updated_count = 0
        for row in rows:
            updates: dict[str, str] = {}
            recovered: list[str] = []
            unavailable: list[str] = []
            content_hash_verified = False

            if row["content"]:
                content_hash_verified = (
                    hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
                    == row["content_sha256"]
                )
            else:
                lesson = str(row["job_lesson"] or "")
                if lesson and hashlib.sha256(lesson.encode("utf-8")).hexdigest() == row["content_sha256"]:
                    updates["content"] = lesson
                    recovered.append("content")
                    content_hash_verified = True
                else:
                    unavailable.append("content_hash_match")

            try:
                existing_urls = json.loads(row["source_urls_json"])
            except (TypeError, json.JSONDecodeError):
                existing_urls = None
            if existing_urls == []:
                evidence_rows = connection.execute(
                    """SELECT source_ref FROM learning_evidence
                       WHERE job_id=? AND evidence_type='web_page' ORDER BY id""",
                    (row["job_id"],),
                ).fetchall()
                recovered_urls = list(dict.fromkeys(
                    str(item["source_ref"])
                    for item in evidence_rows
                    if isinstance(item["source_ref"], str)
                    and item["source_ref"].startswith("https://")
                ))
                if recovered_urls:
                    updates["source_urls_json"] = json.dumps(recovered_urls, ensure_ascii=False)
                    recovered.append("source_urls")
                else:
                    unavailable.append("source_urls")

            if row["quality_report_json"] == "{}":
                try:
                    quality = json.loads(row["job_quality_json"]) if row["job_quality_json"] else None
                except (TypeError, json.JSONDecodeError):
                    quality = None
                if isinstance(quality, dict) and quality:
                    updates["quality_report_json"] = json.dumps(
                        quality, ensure_ascii=False, sort_keys=True
                    )
                    recovered.append("quality_report")
                else:
                    unavailable.append("quality_report")

            if not row["source_text_exists"]:
                unavailable.append("source_text")
            if not row["job_assessment_json"]:
                unavailable.append("assessment")
            if row["test_plan_json"] == "[]":
                unavailable.append("test_plan")
            if not row["knowledge_item_exists"]:
                unavailable.append("knowledge_item_version")

            report = {
                "candidate_id": int(row["id"]),
                "job_id": int(row["job_id"]),
                "content_hash_verified": content_hash_verified,
                "recovered_fields": recovered,
                "unavailable_fields": unavailable,
                "mode": "apply" if apply else "dry_run",
            }
            if not updates:
                report["result"] = "no_recoverable_fields"
                reports.append(report)
                continue

            marker = (
                " legacy_recovery: restored="
                + ",".join(recovered)
                + "; unavailable="
                + ",".join(unavailable)
            )
            failure_reason = str(row["failure_reason"] or "") + marker
            if apply:
                assignments = [f"{field}=?" for field in updates]
                values = list(updates.values())
                assignments.extend(["failure_reason=?", "updated_at=CURRENT_TIMESTAMP"])
                values.extend([failure_reason, int(row["id"]), row["content_sha256"], row["failure_reason"]])
                result = connection.execute(
                    f"""UPDATE temporary_knowledge_candidates
                        SET {", ".join(assignments)}
                        WHERE id=? AND content_sha256=? AND failure_reason=?
                          AND status='failed' AND promoted_knowledge_id IS NULL""",
                    values,
                )
                if result.rowcount != 1:
                    report["result"] = "concurrent_change_skipped"
                else:
                    updated_count += 1
                    report["result"] = "recovered"
            else:
                report["result"] = "recovery_available"
            reports.append(report)

    return {
        "mode": "apply" if apply else "dry_run",
        "database_path": str(db.defaults.learning_db_path),
        "scanned": len(reports),
        "updated": updated_count,
        "candidates": reports,
        "deletions": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or safely recover hash-verifiable fields from legacy rejected learning candidates."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write only exact-hash-matching lesson content and preserved source/quality metadata; default is read-only",
    )
    args = parser.parse_args()
    print(json.dumps(repair_legacy_rejected_candidates(apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
