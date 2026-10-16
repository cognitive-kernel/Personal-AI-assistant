"""Reviewed, versioned SQLite schema definitions and safe legacy initialization.

Application startup uses this registry. Legacy schemas are adopted only after
matching a reviewed canonical fingerprint; unknown schemas fail closed.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .schema_migrations import (
    Migration,
    MigrationRunner,
    UntrackedSchemaError,
    record_baseline,
    schema_fingerprint,
)


SETTINGS_MIGRATIONS = (
    Migration(
        1,
        "settings-initial",
        (
            """CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        ),
    ),
    Migration(
        2,
        "sandbox-execution-consent",
        (
            """CREATE TABLE sandbox_execution_consent (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                policy_version TEXT NOT NULL DEFAULT 'sandbox-execution-v1',
                consented_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(enabled = 0 OR (consented_at IS NOT NULL AND revoked_at IS NULL))
            )""",
            """CREATE TABLE sandbox_execution_consent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL
                    CHECK(event_type IN ('consent_granted', 'consent_revoked')),
                policy_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """INSERT INTO sandbox_execution_consent(
                   id, enabled, policy_version, updated_at
               ) VALUES (1, 0, 'sandbox-execution-v1', CURRENT_TIMESTAMP)""",
        ),
    ),
    Migration(
        3,
        "opt-in-experience-auto-capture-consent",
        (
            """CREATE TABLE experience_auto_capture_consent (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                policy_version TEXT NOT NULL DEFAULT 'experience-auto-capture-v1',
                consented_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(enabled = 0 OR (consented_at IS NOT NULL AND revoked_at IS NULL))
            )""",
            """CREATE TABLE experience_auto_capture_consent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL
                    CHECK(event_type IN ('consent_granted', 'consent_revoked')),
                policy_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """INSERT INTO experience_auto_capture_consent(
                   id, enabled, policy_version, updated_at
               ) VALUES (1, 0, 'experience-auto-capture-v1', CURRENT_TIMESTAMP)""",
        ),
    ),
)

LEARNING_MIGRATIONS = (
    Migration(
        1,
        "learning-initial",
        (
            """CREATE TABLE learning_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                curriculum_slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_path_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                position INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                latest_score REAL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(path_id, topic),
                UNIQUE(path_id, position)
            )""",
            """CREATE TABLE learning_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                lesson TEXT,
                assessment_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                evidence_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                content_hash TEXT,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE learning_source_text (
                job_id INTEGER PRIMARY KEY REFERENCES learning_jobs(id) ON DELETE CASCADE,
                content TEXT NOT NULL
            )""",
            """CREATE TABLE knowledge_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'unverified',
                job_id INTEGER NOT NULL UNIQUE REFERENCES learning_jobs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(topic, version)
            )""",
            """CREATE TABLE assessment_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                answers_json TEXT NOT NULL,
                score REAL NOT NULL,
                correct_count INTEGER NOT NULL,
                question_count INTEGER NOT NULL,
                scoring_method TEXT NOT NULL DEFAULT 'normalized_exact_match',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE lesson_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                outcomes_json TEXT NOT NULL,
                value_rating INTEGER NOT NULL CHECK(value_rating BETWEEN 1 AND 5),
                takeaway TEXT NOT NULL DEFAULT '',
                issues TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL CHECK(length(trim(task_type)) > 0),
                payload_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','pause_requested','paused',
                                     'cancel_requested','cancelled','succeeded','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK(max_attempts >= 1),
                checkpoint_json TEXT,
                result_json TEXT,
                error TEXT,
                scheduled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                lease_owner TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_learning_jobs_status ON learning_jobs(status)",
            "CREATE INDEX idx_knowledge_topic ON knowledge_items(topic, version DESC)",
            "CREATE INDEX idx_assessment_attempts_job ON assessment_attempts(job_id, id DESC)",
            "CREATE INDEX idx_lesson_reviews_job ON lesson_reviews(job_id, id DESC)",
            "CREATE INDEX idx_path_topics_status ON learning_path_topics(path_id, status, position)",
            "CREATE INDEX idx_task_queue_ready ON task_queue(status, scheduled_at, priority DESC, id)",
            "CREATE INDEX idx_task_queue_lease ON task_queue(status, lease_expires_at)",
        ),
    ),
    Migration(
        2,
        "learning-job-quality-json",
        ("ALTER TABLE learning_jobs ADD COLUMN quality_json TEXT",),
    ),
    Migration(
        3,
        "learning-path-topic-and-task-idempotency",
        (
            "ALTER TABLE learning_jobs ADD COLUMN path_topic_id INTEGER REFERENCES learning_path_topics(id) ON DELETE SET NULL",
            "ALTER TABLE task_queue ADD COLUMN dedupe_key TEXT",
            "CREATE INDEX idx_learning_jobs_path_topic ON learning_jobs(path_topic_id, status)",
            "CREATE UNIQUE INDEX idx_task_queue_dedupe_key ON task_queue(dedupe_key) WHERE dedupe_key IS NOT NULL",
        ),
    ),
    Migration(
        4,
        "learning-job-lesson-review-context",
        ("ALTER TABLE learning_jobs ADD COLUMN lesson_review_json TEXT",),
    ),
    Migration(
        5,
        "learning-experience-memory",
        (
            """CREATE TABLE IF NOT EXISTS learning_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'identified'
                    CHECK(status IN ('identified','researching','ready_to_test','testing',
                                     'verified','failed','needs_review')),
                source_evidence_json TEXT NOT NULL DEFAULT '[]',
                test_plan_json TEXT NOT NULL DEFAULT '[]',
                test_results_json TEXT NOT NULL DEFAULT '[]',
                takeaway TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS idx_learning_experiences_topic ON learning_experiences(topic, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_learning_experiences_status ON learning_experiences(status, id DESC)",
            """CREATE TABLE IF NOT EXISTS learning_experience_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id INTEGER NOT NULL REFERENCES learning_experiences(id) ON DELETE CASCADE,
                version INTEGER NOT NULL CHECK(version >= 1),
                topic TEXT NOT NULL,
                problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                status TEXT NOT NULL,
                source_evidence_json TEXT NOT NULL,
                test_plan_json TEXT NOT NULL,
                test_results_json TEXT NOT NULL,
                takeaway TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experience_id, version)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_experience_versions_lookup ON learning_experience_versions(experience_id, version DESC)",
        ),
    ),
    Migration(
        6,
        "learning-experience-embedding-cache",
        (
            """CREATE TABLE IF NOT EXISTS learning_experience_embeddings (
                experience_id INTEGER NOT NULL REFERENCES learning_experiences(id) ON DELETE CASCADE,
                experience_version INTEGER NOT NULL CHECK(experience_version >= 1),
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(experience_id, experience_version, model)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_experience_embeddings_model ON learning_experience_embeddings(model, experience_version)",
        ),
    ),
    Migration(
        7,
        "chat-research-audit-metadata",
        (
            """CREATE TABLE IF NOT EXISTS chat_research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_sha256 TEXT NOT NULL CHECK(length(query_sha256) = 64),
                answer_sha256 TEXT NOT NULL CHECK(length(answer_sha256) = 64),
                route TEXT NOT NULL CHECK(route IN ('technical_research')),
                source_count INTEGER NOT NULL CHECK(source_count >= 0),
                sources_json TEXT NOT NULL DEFAULT '[]',
                review_status TEXT NOT NULL CHECK(review_status IN (
                    'no_sources','passed_model_assisted','passed_model_assisted_after_repair',
                    'needs_review','review_unavailable','generation_failed'
                )),
                review_summary_json TEXT,
                memory_refs_json TEXT NOT NULL DEFAULT '[]',
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS idx_chat_research_runs_created ON chat_research_runs(created_at DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_research_runs_review ON chat_research_runs(review_status, created_at DESC)",
        ),
    ),
)


CLOUD_RESOURCE_MIGRATIONS = (
    Migration(
        1,
        "cloud-resource-discovery-initial",
        (
            """CREATE TABLE cloud_resource_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL UNIQUE,
                provider_domain TEXT NOT NULL,
                title TEXT NOT NULL,
                content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
                resource_signals_json TEXT NOT NULL DEFAULT '[]',
                evidence_excerpt TEXT NOT NULL DEFAULT '',
                discovery_query TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'unreviewed'
                    CHECK(review_status IN ('unreviewed','approved','rejected')),
                availability_status TEXT NOT NULL DEFAULT 'unknown'
                    CHECK(availability_status IN ('unknown','available','unavailable','requires_auth')),
                quota_json TEXT NOT NULL DEFAULT '{}',
                terms_url TEXT,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_cloud_candidates_domain ON cloud_resource_candidates(provider_domain, last_seen_at DESC)",
            "CREATE INDEX idx_cloud_candidates_review ON cloud_resource_candidates(review_status, last_seen_at DESC)",
            """CREATE TABLE cloud_resource_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running','completed','partial','failed')),
                queries_attempted INTEGER NOT NULL DEFAULT 0 CHECK(queries_attempted >= 0),
                sources_seen INTEGER NOT NULL DEFAULT 0 CHECK(sources_seen >= 0),
                candidates_upserted INTEGER NOT NULL DEFAULT 0 CHECK(candidates_upserted >= 0),
                query_results_json TEXT NOT NULL DEFAULT '[]',
                error_summary TEXT
            )""",
            "CREATE INDEX idx_cloud_discovery_runs_started ON cloud_resource_discovery_runs(started_at DESC, id DESC)",
            "CREATE INDEX idx_cloud_discovery_runs_status ON cloud_resource_discovery_runs(status, started_at DESC)",
        ),
    ),
    Migration(
        2,
        "cloud-resource-review-and-freshness",
        (
            """ALTER TABLE cloud_resource_candidates ADD COLUMN freshness_status TEXT NOT NULL
               DEFAULT 'fresh' CHECK(freshness_status IN ('fresh','stale','unknown'))""",
            "ALTER TABLE cloud_resource_candidates ADD COLUMN reviewed_at TEXT",
            """ALTER TABLE cloud_resource_candidates ADD COLUMN reviewed_content_sha256 TEXT
               CHECK(reviewed_content_sha256 IS NULL OR length(reviewed_content_sha256) = 64)""",
            """UPDATE cloud_resource_candidates
               SET review_status='unreviewed', availability_status='unknown',
                   quota_json='{}', terms_url=NULL
               WHERE review_status != 'unreviewed'""",
            "CREATE INDEX idx_cloud_candidates_freshness ON cloud_resource_candidates(freshness_status, last_seen_at DESC)",
            "CREATE INDEX idx_cloud_candidates_reviewed ON cloud_resource_candidates(reviewed_at DESC)",
        ),
    ),
    Migration(
        3,
        "cloud-resource-evidence-claims",
        (
            """CREATE TABLE cloud_resource_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL REFERENCES cloud_resource_candidates(id) ON DELETE CASCADE,
                source_content_sha256 TEXT NOT NULL CHECK(length(source_content_sha256) = 64),
                claim_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                unit TEXT,
                evidence_quote TEXT NOT NULL,
                adapter_id TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'unreviewed'
                    CHECK(review_status IN ('unreviewed','accepted','rejected')),
                is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
                extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(candidate_id, source_content_sha256, claim_type, value_json, evidence_quote)
            )""",
            "CREATE INDEX idx_cloud_claims_current ON cloud_resource_claims(candidate_id, is_current, claim_type)",
            "CREATE INDEX idx_cloud_claims_review ON cloud_resource_claims(review_status, is_current, extracted_at DESC)",
        ),
    ),
    Migration(
        4,
        "cloud-resource-claim-review-audit",
        ("ALTER TABLE cloud_resource_claims ADD COLUMN reviewed_at TEXT",),
    ),
    Migration(
        5,
        "cloud-provider-consent-and-quota-snapshots",
        (
            """CREATE TABLE provider_access_consents (
                provider_id TEXT PRIMARY KEY CHECK(provider_id = 'kaggle'),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                user_consent INTEGER NOT NULL DEFAULT 0 CHECK(user_consent IN (0,1)),
                executable_path TEXT NOT NULL DEFAULT '',
                consent_version TEXT NOT NULL DEFAULT 'kaggle-quota-readonly-v1',
                consented_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(enabled = 0 OR
                      (user_consent = 1 AND length(trim(executable_path)) > 0
                       AND consented_at IS NOT NULL))
            )""",
            """CREATE TABLE provider_access_consent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL CHECK(provider_id = 'kaggle'),
                event_type TEXT NOT NULL CHECK(event_type IN ('consent_granted','consent_revoked')),
                consent_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE provider_quota_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL CHECK(provider_id = 'kaggle'),
                resource TEXT NOT NULL CHECK(resource IN ('GPU','TPU')),
                used_hours REAL NOT NULL CHECK(used_hours >= 0),
                remaining_hours REAL NOT NULL CHECK(remaining_hours >= 0),
                total_hours REAL NOT NULL CHECK(total_hours >= 0),
                observed_at TEXT NOT NULL,
                refresh_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_provider_consent_events_created ON provider_access_consent_events(provider_id, created_at DESC, id DESC)",
            "CREATE INDEX idx_provider_quota_latest ON provider_quota_snapshots(provider_id, resource, observed_at DESC, id DESC)",
        ),
    ),
    Migration(
        6,
        "oci-readonly-limit-consent-and-snapshots",
        (
            """CREATE TABLE oci_limit_access_consents (
                provider_id TEXT PRIMARY KEY CHECK(provider_id = 'oracle_cloud'),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                user_consent INTEGER NOT NULL DEFAULT 0 CHECK(user_consent IN (0,1)),
                executable_path TEXT NOT NULL DEFAULT '',
                config_file TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                compartment_id TEXT NOT NULL DEFAULT '',
                consent_version TEXT NOT NULL DEFAULT 'oci-readonly-limits-v1',
                consented_at TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(enabled = 0 OR (
                    user_consent = 1 AND length(trim(executable_path)) > 0
                    AND length(trim(config_file)) > 0 AND length(trim(profile)) > 0
                    AND length(trim(region)) > 0 AND length(trim(compartment_id)) > 0
                    AND consented_at IS NOT NULL
                ))
            )""",
            """CREATE TABLE oci_limit_consent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL CHECK(provider_id = 'oracle_cloud'),
                event_type TEXT NOT NULL CHECK(event_type IN ('consent_granted','consent_revoked')),
                consent_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE oci_limit_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id TEXT NOT NULL CHECK(provider_id = 'oracle_cloud'),
                resource_name TEXT NOT NULL,
                limit_value REAL NOT NULL CHECK(limit_value >= 0),
                used_value REAL CHECK(used_value IS NULL OR used_value >= 0),
                available_value REAL CHECK(available_value IS NULL OR available_value >= 0),
                scope_type TEXT NOT NULL DEFAULT '',
                scope_value TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider_id, resource_name, scope_type, scope_value, observed_at)
            )""",
            "CREATE INDEX idx_oci_limit_consent_events_created ON oci_limit_consent_events(created_at DESC, id DESC)",
            "CREATE INDEX idx_oci_limit_snapshots_latest ON oci_limit_snapshots(provider_id, resource_name, observed_at DESC, id DESC)",
        ),
    ),
    Migration(
        7,
        "cloud-resource-selection-plan-history",
        (
            """CREATE TABLE cloud_resource_selection_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_version TEXT NOT NULL,
                requirements_json TEXT NOT NULL,
                limitations_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL CHECK(status IN ('candidate_found','no_eligible_candidate')),
                selected_candidate_id INTEGER,
                execution_authorized INTEGER NOT NULL DEFAULT 0 CHECK(execution_authorized = 0),
                remote_execution_enabled INTEGER NOT NULL DEFAULT 0 CHECK(remote_execution_enabled = 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_cloud_selection_plans_created ON cloud_resource_selection_plans(created_at DESC, id DESC)",
            """CREATE TABLE cloud_resource_selection_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL REFERENCES cloud_resource_selection_plans(id) ON DELETE CASCADE,
                candidate_id INTEGER,
                provider_domain TEXT,
                content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256) = 64),
                eligible_for_adapter_review INTEGER NOT NULL CHECK(eligible_for_adapter_review IN (0,1)),
                preflight_status TEXT NOT NULL CHECK(preflight_status IN ('blocked','needs_review','ready_for_provider_adapter')),
                capabilities_json TEXT NOT NULL DEFAULT '{}',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                policy_blockers_json TEXT NOT NULL DEFAULT '[]',
                policy_review_needed_json TEXT NOT NULL DEFAULT '[]',
                evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_cloud_selection_decisions_plan ON cloud_resource_selection_decisions(plan_id, eligible_for_adapter_review, id)",
            "CREATE INDEX idx_cloud_selection_decisions_candidate ON cloud_resource_selection_decisions(candidate_id, evaluated_at DESC)",
        ),
    ),
    Migration(
        8,
        "cloud-resource-plan-reevaluation-metadata",
        (
            """ALTER TABLE cloud_resource_selection_plans ADD COLUMN plan_origin TEXT NOT NULL
               DEFAULT 'manual' CHECK(plan_origin IN ('manual','scheduled_reevaluation'))""",
            "ALTER TABLE cloud_resource_selection_plans ADD COLUMN parent_plan_id INTEGER",
            "ALTER TABLE cloud_resource_selection_plans ADD COLUMN reevaluation_key TEXT",
            "CREATE INDEX idx_cloud_selection_plans_origin_created ON cloud_resource_selection_plans(plan_origin, created_at DESC, id DESC)",
            """CREATE UNIQUE INDEX idx_cloud_selection_plans_reevaluation_once
               ON cloud_resource_selection_plans(parent_plan_id, reevaluation_key)
               WHERE plan_origin='scheduled_reevaluation'
                 AND parent_plan_id IS NOT NULL AND reevaluation_key IS NOT NULL""",
        ),
    ),
)


def fingerprint_for_version(migrations: Iterable[Migration], version: int) -> str:
    """Build a canonical schema fixture in memory and return its exact fingerprint."""
    selected = tuple(migration for migration in migrations if migration.version <= version)
    if not selected or selected[-1].version != version:
        raise ValueError(f"No migration registry reaches version {version}.")
    with sqlite3.connect(":memory:") as connection:
        MigrationRunner(selected).apply(connection)
        return schema_fingerprint(connection)



def initialize_connection(connection: sqlite3.Connection, migrations: Iterable[Migration]) -> int:
    """Initialize a fresh DB or adopt an exact known migration schema before upgrading it."""
    migration_list = tuple(migrations)
    runner = MigrationRunner(migration_list)
    history_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone() is not None
    has_user_schema = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'
        LIMIT 1
        """
    ).fetchone() is not None
    if history_exists or not has_user_schema:
        return runner.apply(connection)

    actual = schema_fingerprint(connection)
    for migration in migration_list:
        expected = fingerprint_for_version(migration_list, migration.version)
        if actual == expected:
            record_baseline(
                connection,
                migration=migration,
                expected_fingerprint=expected,
            )
            return runner.apply(connection)
    raise UntrackedSchemaError(
        "Existing database schema does not match any registered migration version; "
        "no history was written and no schema changes were applied."
    )



def _needs_schema_backup(path: Path, migrations: tuple[Migration, ...]) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with closing(sqlite3.connect(str(path))) as connection:
        history_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is not None
        has_user_schema = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type IN ('table', 'index', 'view', 'trigger')
              AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'
            LIMIT 1
            """
        ).fetchone() is not None
        if not has_user_schema:
            return False
        if not history_exists:
            return True
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)")
        }
        if "version" not in columns:
            return True
        try:
            current_version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            return current_version is None or int(current_version) < migrations[-1].version
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return True


def _create_pre_migration_backup(path: Path) -> Path:
    """Create and verify a unique SQLite backup before any schema adoption or upgrade."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = path.with_name(f"{path.name}.pre-migration-{stamp}.bak")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.pre-migration-{stamp}-{suffix}.bak")
        suffix += 1

    with closing(sqlite3.connect(str(path))) as source, closing(
        sqlite3.connect(str(candidate))
    ) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"Pre-migration backup failed integrity validation: {candidate}")
    return candidate


def initialize_database_file(path: str | Path, migrations: Iterable[Migration]) -> int:
    """Back up existing schemas before baseline adoption/upgrades, then initialize safely."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    migration_list = tuple(migrations)
    if not migration_list:
        raise ValueError("At least one migration must be registered.")

    if _needs_schema_backup(target, migration_list):
        _create_pre_migration_backup(target)

    with closing(sqlite3.connect(str(target), timeout=10)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return initialize_connection(connection, migration_list)



def restore_database_backup(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Restore a verified SQLite backup atomically; replacing a target requires opt-in."""
    source_path = Path(backup_path)
    target = Path(target_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite backup does not exist: {source_path}")
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"Restore target already exists; pass overwrite=True to replace it: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.restore-", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(str(source_path))) as source:
            integrity = source.execute("PRAGMA integrity_check").fetchone()
            violations = source.execute("PRAGMA foreign_key_check").fetchall()
            if integrity is None or integrity[0] != "ok" or violations:
                raise RuntimeError(f"Backup failed validation and cannot be restored: {source_path}")
            with closing(sqlite3.connect(str(temporary))) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()
                violations = destination.execute("PRAGMA foreign_key_check").fetchall()
                if integrity is None or integrity[0] != "ok" or violations:
                    raise RuntimeError(f"Restored database failed validation: {temporary}")
        if target.exists() and not overwrite:
            raise FileExistsError(f"Restore target appeared during restore: {target}")
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            temporary.unlink()
