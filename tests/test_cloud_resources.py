from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import cloud_resources, db, task_manager, web_sources
from app.cloud_resources import CloudResourceError
from app.schema_migrations import MigrationRunner
from app.schema_registry import CLOUD_RESOURCE_MIGRATIONS, initialize_database_file


def _configure_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    db.init_databases()
    task_manager.initialize_task_store()
    cloud_resources.initialize_store()


def _document(url="https://compute.example.org/free-tier", title="Free GPU compute", text=None):
    return web_sources.SourceDocument(
        url=url,
        title=title,
        text=text or (
            "Free tier GPU compute credits and quota limits. Read the official terms "
            "and usage policy before choosing a provider. " * 4
        ),
    )


def test_cloud_resource_store_uses_its_own_versioned_database(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    assert db.defaults.cloud_resources_db_path != db.defaults.learning_db_path
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "schema_migrations",
            "cloud_resource_candidates",
            "cloud_resource_discovery_runs",
            "cloud_resource_claims",
            "cloud_resource_selection_plans",
            "cloud_resource_selection_decisions",
        } <= tables
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 8
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_discovery_persists_provenance_and_keeps_candidates_unreviewed(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    calls = []

    def fake_discover(query, *, limit):
        calls.append((query, limit))
        return [_document()]

    report = cloud_resources.discover_resources(discover_fn=fake_discover)

    assert report["status"] == "completed"
    assert report["queries_attempted"] == len(cloud_resources.DISCOVERY_QUERIES)
    assert report["sources_seen"] == len(cloud_resources.DISCOVERY_QUERIES)
    assert report["candidates_upserted"] == 1
    assert all(limit == cloud_resources.MAX_RESULTS_PER_QUERY for _, limit in calls)

    candidate = cloud_resources.list_candidates()[0]
    assert candidate["provider_domain"] == "compute.example.org"
    assert candidate["source_url"] == "https://compute.example.org/free-tier"
    assert len(candidate["content_sha256"]) == 64
    assert candidate["review_status"] == "unreviewed"
    assert candidate["availability_status"] == "unknown"
    assert candidate["freshness_status"] == "fresh"
    assert candidate["reviewed_content_sha256"] is None
    assert candidate["quota"] == {}
    assert "gpu_or_accelerator_mentioned" in candidate["resource_signals"]
    assert "free_tier_or_credits_mentioned" in candidate["resource_signals"]
    assert candidate["evidence_excerpt"]
    assert any(claim["claim_type"] == "free_tier_mention" for claim in candidate["observed_claims"])
    assert all(claim["review_status"] == "unreviewed" for claim in candidate["observed_claims"])
    assert candidate["eligibility_assessment"]["status"] == "blocked"
    assert candidate["eligibility_assessment"]["execution_authorized"] is False
    latest = cloud_resources.latest_discovery_run()
    assert latest["id"] == report["run_id"]
    assert latest["status"] == "completed"
    assert len(latest["query_results"]) == len(cloud_resources.DISCOVERY_QUERIES)


def test_refresh_preserves_review_only_when_source_content_is_unchanged(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    first = _document(text="Free tier GPU compute quota. Official usage terms. " * 4)
    changed = _document(text=("CPU compute credits and storage quota. Official usage terms. "
                              "2 vCPU and 16 GB RAM. " * 4))
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [first]
    )
    cloud_resources.discover_resources()
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET review_status='approved', availability_status='available',
                   quota_json=?, terms_url=?, reviewed_at=CURRENT_TIMESTAMP,
                   reviewed_content_sha256=content_sha256 WHERE source_url=?""",
            (json.dumps({"cpu": "2 vCPU"}), "https://compute.example.org/terms", first.url),
        )
        connection.commit()

    # Re-observing identical evidence must not erase an explicit review decision.
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    assert candidate["review_status"] == "approved"
    assert candidate["availability_status"] == "available"
    assert candidate["quota"] == {"cpu": "2 vCPU"}
    assert candidate["terms_url"] == "https://compute.example.org/terms"

    # A changed page invalidates review and quota claims tied to the old content hash.
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [changed]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    assert candidate["content_sha256"] == hashlib.sha256(
        f"{changed.title}\n{' '.join(changed.text.split())}".encode()
    ).hexdigest()
    assert candidate["review_status"] == "unreviewed"
    assert candidate["availability_status"] == "unknown"
    assert candidate["quota"] == {}
    assert candidate["terms_url"] is None
    assert candidate["reviewed_at"] is None
    assert candidate["reviewed_content_sha256"] is None
    assert any(claim["claim_type"] == "cpu_count" for claim in candidate["observed_claims"])
    assert any(claim["claim_type"] == "memory_capacity" for claim in candidate["observed_claims"])
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        history = connection.execute(
            "SELECT source_content_sha256, is_current FROM cloud_resource_claims WHERE candidate_id=?",
            (candidate["id"],),
        ).fetchall()
    assert any(content_hash != candidate["content_sha256"] and current == 0 for content_hash, current in history)
    assert all(content_hash == candidate["content_sha256"] and current == 1
               for content_hash, current in history if current == 1)


def test_discovery_rejects_http_sources_and_records_empty_runs(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources,
        "discover_sources",
        lambda query, limit=3: [
            _document(url="http://compute.example.org/free"),
            _document(url="https://compute.example.org/short", text="Too short"),
        ],
    )

    report = cloud_resources.discover_resources()

    assert report["status"] == "failed"
    assert report["candidates_upserted"] == 0
    assert "No valid public HTTPS" in report["error_summary"]
    assert cloud_resources.list_candidates() == []


def test_daily_discovery_enqueue_is_idempotent(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    first = cloud_resources.ensure_daily_discovery_scheduled()
    second = cloud_resources.ensure_daily_discovery_scheduled()

    assert first["id"] == second["id"]
    assert first["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert first["status"] == "queued"
    assert first["payload"] == {"trigger": "scheduled"}


def test_daily_discovery_recovers_a_pre_run_cancelled_task(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    first = cloud_resources.ensure_daily_discovery_scheduled()
    cancelled = task_manager.request_cancel(first["id"])
    assert cancelled["status"] == "cancelled"

    recovery = cloud_resources.ensure_daily_discovery_scheduled()

    assert recovery["id"] != first["id"]
    assert recovery["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert recovery["status"] == "queued"


def test_discovery_handler_schedules_next_daily_pass(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cloud_resources,
        "discover_resources",
        lambda checkpoint=None: {
            "run_id": 1,
            "status": "completed",
            "queries_attempted": 3,
            "sources_seen": 2,
            "candidates_upserted": 2,
            "error_summary": None,
        },
    )

    class Context:
        def checkpoint(self, data):
            return data

    result = cloud_resources.handle_discovery_task(
        {"trigger": "manual"}, None, Context()
    )
    next_task = task_manager.get_task(result["next_scheduled_task_id"])
    assert next_task["task_type"] == cloud_resources.CLOUD_DISCOVERY_TASK_TYPE
    assert next_task["status"] == "queued"
    assert next_task["payload"] == {"trigger": "scheduled"}
    assert datetime.fromisoformat(result["next_scheduled_at"]).tzinfo is not None


def test_cloud_discovery_migrations_can_initialize_a_fresh_database():
    with sqlite3.connect(":memory:") as connection:
        from app.schema_migrations import MigrationRunner
        assert MigrationRunner(CLOUD_RESOURCE_MIGRATIONS).apply(connection) == 8
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"



def test_review_candidate_requires_current_hash_and_https_terms(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    document = _document()
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]

    reviewed = cloud_resources.review_candidate(
        candidate["id"],
        expected_content_sha256=candidate["content_sha256"],
        review_status="approved",
        availability_status="available",
        quota={"cpu": "2 vCPU", "hours_per_month": 100},
        terms_url="https://compute.example.org/terms",
    )
    assert reviewed["review_status"] == "approved"
    assert reviewed["availability_status"] == "available"
    assert reviewed["reviewed_content_sha256"] == candidate["content_sha256"]
    assert reviewed["reviewed_at"]
    assert reviewed["quota"] == {"cpu": "2 vCPU", "hours_per_month": 100}

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="content changed"):
        cloud_resources.review_candidate(
            candidate["id"],
            expected_content_sha256="0" * 64,
            review_status="approved",
            availability_status="available",
            terms_url="https://compute.example.org/terms",
        )
    with pytest.raises(cloud_resources.CloudResourceError, match="HTTPS"):
        cloud_resources.review_candidate(
            candidate["id"],
            expected_content_sha256=candidate["content_sha256"],
            review_status="approved",
            availability_status="available",
            terms_url="http://compute.example.org/terms",
        )


def test_stale_candidate_loses_availability_and_cannot_be_reviewed(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [_document()]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET review_status='approved', availability_status='available',
                   reviewed_at=CURRENT_TIMESTAMP, reviewed_content_sha256=content_sha256,
                   last_seen_at=datetime('now', '-31 days')
               WHERE id=?""",
            (candidate["id"],),
        )
        connection.commit()

    assert cloud_resources.mark_stale_candidates(max_age_days=30) == 1
    stale = cloud_resources.list_candidates()[0]
    assert stale["freshness_status"] == "stale"
    assert stale["availability_status"] == "unknown"
    with pytest.raises(cloud_resources.CloudResourceConflictError, match="stale"):
        cloud_resources.review_candidate(
            stale["id"],
            expected_content_sha256=stale["content_sha256"],
            review_status="approved",
            availability_status="available",
            terms_url="https://compute.example.org/terms",
        )


def test_cloud_resource_v1_upgrade_invalidates_legacy_approval(tmp_path):
    path = tmp_path / "legacy-cloud.db"
    with sqlite3.connect(path) as connection:
        assert MigrationRunner(CLOUD_RESOURCE_MIGRATIONS[:1]).apply(connection) == 1
        connection.execute(
            """INSERT INTO cloud_resource_candidates(
                   source_url, provider_domain, title, content_sha256,
                   review_status, availability_status, quota_json, terms_url, discovery_query
               ) VALUES (?, ?, ?, ?, 'approved', 'available', ?, ?, ?)""",
            (
                "https://compute.example.org/free",
                "compute.example.org",
                "Free compute",
                "a" * 64,
                json.dumps({"cpu": "2 vCPU"}),
                "https://compute.example.org/terms",
                "free compute",
            ),
        )
        connection.commit()

    assert initialize_database_file(path, CLOUD_RESOURCE_MIGRATIONS) == 8
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """SELECT review_status, availability_status, quota_json, terms_url,
                      freshness_status, reviewed_content_sha256
               FROM cloud_resource_candidates WHERE source_url=?""",
            ("https://compute.example.org/free",),
        ).fetchone()
    assert row == ("unreviewed", "unknown", "{}", None, "fresh", None)


def test_claim_acceptance_requires_approved_source_and_exact_current_hash(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    document = _document(text=(
        "Free tier includes 2 vCPU and 8 GB RAM. Read terms before use. " * 4
    ))
    monkeypatch.setattr(
        web_sources, "discover_sources", lambda query, limit=3: [document]
    )
    cloud_resources.discover_resources()
    candidate = cloud_resources.list_candidates()[0]
    claim = next(
        item for item in candidate["observed_claims"]
        if item["claim_type"] == "cpu_count"
    )

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="approve the candidate"):
        cloud_resources.review_claim(
            candidate["id"],
            claim["id"],
            expected_source_content_sha256=candidate["content_sha256"],
            review_status="accepted",
        )

    cloud_resources.review_candidate(
        candidate["id"],
        expected_content_sha256=candidate["content_sha256"],
        review_status="approved",
        availability_status="unknown",
        terms_url="https://compute.example.org/terms",
    )
    reviewed = cloud_resources.review_claim(
        candidate["id"],
        claim["id"],
        expected_source_content_sha256=candidate["content_sha256"],
        review_status="accepted",
    )
    assert reviewed["review_status"] == "accepted"
    assert reviewed["reviewed_at"]
    assert reviewed["source_content_sha256"] == candidate["content_sha256"]

    with pytest.raises(cloud_resources.CloudResourceConflictError, match="evidence changed"):
        cloud_resources.review_claim(
            candidate["id"],
            claim["id"],
            expected_source_content_sha256="0" * 64,
            review_status="rejected",
        )



def test_selection_plan_history_persists_only_minimal_decision_snapshot(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    plan = {
        "plan_version": "provider-neutral-selection-v1",
        "status": "candidate_found",
        "selected_candidate_id": 12,
        "execution_authorized": False,
        "remote_execution_enabled": False,
        "requirements": {
            "min_cpu_count": 2,
            "min_memory_gib": 8.0,
            "require_gpu": False,
            "gpu_models": [],
            "allowed_provider_domains": [],
        },
        "limitations": ["No remote execution."],
        "candidates": [
            {
                "candidate_id": 12,
                "provider_domain": "compute.example.org",
                "content_sha256": "a" * 64,
                "eligible_for_adapter_review": True,
                "execution_authorized": False,
                "preflight_status": "ready_for_provider_adapter",
                "capabilities": {"cpu_count": 4, "memory_gib": 16.0, "gpu_models": []},
                "reasons": [],
                "policy_blockers": [],
                "policy_review_needed": ["Provider adapter still required."],
            }
        ],
    }

    saved = cloud_resources.save_resource_selection_plan(plan)
    fetched = cloud_resources.get_resource_selection_plan(saved["plan_id"])
    listed = cloud_resources.list_resource_selection_plans(limit=10)

    assert fetched["status"] == "candidate_found"
    assert fetched["selected_candidate_id"] == 12
    assert fetched["requirements"] == plan["requirements"]
    assert fetched["candidates"][0]["content_sha256"] == "a" * 64
    assert fetched["candidates"][0]["capabilities"]["memory_gib"] == 16.0
    assert fetched["execution_authorized"] is False
    assert fetched["remote_execution_enabled"] is False
    assert listed[0]["plan_id"] == saved["plan_id"]

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        stored = connection.execute(
            "SELECT requirements_json FROM cloud_resource_selection_plans WHERE id=?",
            (saved["plan_id"],),
        ).fetchone()[0]
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(cloud_resource_selection_decisions)"
            )
        }
    assert "evidence_excerpt" not in columns
    assert "private_key" not in stored


def test_selection_plan_storage_rejects_any_execution_authorization(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    plan = {
        "plan_version": "provider-neutral-selection-v1",
        "status": "no_eligible_candidate",
        "selected_candidate_id": None,
        "execution_authorized": True,
        "remote_execution_enabled": False,
        "requirements": {},
        "candidates": [],
    }

    with pytest.raises(CloudResourceError, match="cannot authorize"):
        cloud_resources.save_resource_selection_plan(plan)

    with sqlite3.connect(db.defaults.cloud_resources_db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM cloud_resource_selection_plans"
        ).fetchone()[0]
    assert count == 0



def _empty_resource_plan():
    return {
        "plan_version": "provider-neutral-selection-v1",
        "status": "no_eligible_candidate",
        "selected_candidate_id": None,
        "execution_authorized": False,
        "remote_execution_enabled": False,
        "requirements": {
            "min_cpu_count": 1,
            "min_memory_gib": 0.0,
            "require_gpu": False,
            "gpu_models": [],
            "allowed_provider_domains": [],
        },
        "limitations": ["Local evidence only."],
        "candidates": [],
    }


def test_scheduled_reevaluation_metadata_is_idempotent_per_parent_and_day(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    original = cloud_resources.save_resource_selection_plan(_empty_resource_plan())
    key = "cloud-plan-reevaluation:2026-10-16"

    first = cloud_resources.save_resource_selection_plan(
        _empty_resource_plan(),
        origin="scheduled_reevaluation",
        parent_plan_id=original["plan_id"],
        reevaluation_key=key,
    )
    second = cloud_resources.save_resource_selection_plan(
        _empty_resource_plan(),
        origin="scheduled_reevaluation",
        parent_plan_id=original["plan_id"],
        reevaluation_key=key,
    )

    assert first["plan_id"] == second["plan_id"]
    assert first["origin"] == "scheduled_reevaluation"
    assert first["parent_plan_id"] == original["plan_id"]
    assert first["reevaluation_key"] == key
    assert [item["origin"] for item in cloud_resources.list_resource_selection_plans(origin="manual")] == ["manual"]
    assert [item["plan_id"] for item in cloud_resources.list_resource_selection_plans(origin="scheduled_reevaluation")] == [first["plan_id"]]


def test_daily_plan_reevaluation_schedule_is_idempotent(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)

    first = cloud_resources.ensure_daily_plan_reevaluation_scheduled()
    second = cloud_resources.ensure_daily_plan_reevaluation_scheduled()

    assert first["id"] == second["id"]
    assert first["task_type"] == cloud_resources.CLOUD_PLAN_REEVALUATION_TASK_TYPE
    assert first["dedupe_key"].startswith("cloud-plan-reevaluation:")


def test_plan_reevaluation_handler_rechecks_local_candidates_and_schedules_next_run(
    tmp_path, monkeypatch
):
    _configure_paths(tmp_path, monkeypatch)
    original = cloud_resources.save_resource_selection_plan(_empty_resource_plan())
    monkeypatch.setattr(cloud_resources, "list_candidates", lambda limit=500: [])

    class Context:
        def __init__(self):
            self.checkpoints = []

        def checkpoint(self, value):
            self.checkpoints.append(value)
            return {"status": "running", "checkpoint": value}

    context = Context()
    key = "cloud-plan-reevaluation:2026-10-16"
    result = cloud_resources.handle_plan_reevaluation_task(
        {"trigger": "scheduled", "reevaluation_key": key},
        None,
        context,
    )

    assert result["status"] == "completed"
    assert result["source_plans_considered"] == 1
    assert result["plans_reevaluated"] == 1
    assert result["execution_authorized"] is False
    assert result["remote_execution_enabled"] is False
    reevaluated = cloud_resources.get_resource_selection_plan(result["created_plan_ids"][0])
    assert reevaluated["origin"] == "scheduled_reevaluation"
    assert reevaluated["parent_plan_id"] == original["plan_id"]
    assert reevaluated["reevaluation_key"] == key
    assert context.checkpoints
    next_task = task_manager.get_task_by_dedupe_key(
        f"cloud-plan-reevaluation:{(datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()}"
    )
    assert next_task is not None
