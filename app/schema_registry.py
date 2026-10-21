"""Reviewed, versioned SQLite schema definitions and safe legacy initialization.

Application startup uses this registry. Legacy schemas are adopted only after
matching a reviewed canonical fingerprint; unknown schemas fail closed.
"""
from __future__ import annotations

import hashlib
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
    Migration(
        8,
        "experience-source-revalidation-history",
        (
            """CREATE TABLE learning_experience_source_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id INTEGER NOT NULL REFERENCES learning_experiences(id) ON DELETE CASCADE,
                experience_version INTEGER NOT NULL CHECK(experience_version >= 1),
                source_url TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN (
                    'baseline','unchanged','changed','unavailable','blocked'
                )),
                content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256) = 64),
                previous_content_sha256 TEXT CHECK(
                    previous_content_sha256 IS NULL OR length(previous_content_sha256) = 64
                ),
                title TEXT NOT NULL DEFAULT '',
                error_kind TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_experience_source_checks_latest ON learning_experience_source_checks(experience_id, source_url, checked_at DESC, id DESC)",
        ),
    ),
    Migration(
        9,
        "experience-explicit-retrieval-feedback",
        (
            """CREATE TABLE learning_experience_feedback (
                experience_id INTEGER PRIMARY KEY REFERENCES learning_experiences(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL CHECK(rating IN (-1, 1)),
                updated_at TEXT NOT NULL
            )""",
            "CREATE INDEX idx_experience_feedback_rating ON learning_experience_feedback(rating, updated_at DESC)",
        ),
    ),
    Migration(
        10,
        "learning-semantic-assessment-reviews",
        (
            """CREATE TABLE assessment_semantic_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL UNIQUE REFERENCES assessment_attempts(id) ON DELETE CASCADE,
                job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                review_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX idx_assessment_semantic_reviews_job ON assessment_semantic_reviews(job_id, created_at DESC)",
        ),
    ),
    Migration(
        11,
        "learning-spaced-repetition-schedule",
        (
            """CREATE TABLE learning_review_schedule (
                topic_id INTEGER PRIMARY KEY REFERENCES learning_path_topics(id) ON DELETE CASCADE,
                repetitions INTEGER NOT NULL DEFAULT 0 CHECK(repetitions >= 0),
                interval_days INTEGER NOT NULL DEFAULT 0 CHECK(interval_days >= 0),
                ease_factor REAL NOT NULL DEFAULT 2.5 CHECK(ease_factor >= 1.3),
                due_at TEXT NOT NULL,
                last_reviewed_at TEXT NOT NULL,
                last_rating INTEGER NOT NULL CHECK(last_rating BETWEEN 0 AND 5),
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE learning_review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL REFERENCES learning_path_topics(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL CHECK(rating BETWEEN 0 AND 5),
                previous_interval_days INTEGER NOT NULL CHECK(previous_interval_days >= 0),
                interval_days INTEGER NOT NULL CHECK(interval_days >= 1),
                previous_ease_factor REAL NOT NULL CHECK(previous_ease_factor >= 1.3),
                ease_factor REAL NOT NULL CHECK(ease_factor >= 1.3),
                repetitions INTEGER NOT NULL CHECK(repetitions >= 0),
                due_at TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            )""",
            "CREATE INDEX idx_learning_review_schedule_due ON learning_review_schedule(due_at, topic_id)",
            "CREATE INDEX idx_learning_review_events_topic ON learning_review_events(topic_id, id DESC)",
        ),
    ),
    Migration(
        12,
        "governed-self-improvement-proposals",
        (
            """CREATE TABLE self_improvement_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
                rationale TEXT NOT NULL CHECK(length(trim(rationale)) BETWEEN 1 AND 4000),
                patch_text TEXT NOT NULL,
                patch_sha256 TEXT NOT NULL CHECK(length(patch_sha256) = 64),
                status TEXT NOT NULL DEFAULT 'proposed'
                    CHECK(status IN ('proposed','validated','approved_for_pr','rejected')),
                base_commit_sha TEXT NOT NULL CHECK(length(base_commit_sha) BETWEEN 40 AND 64),
                validation_report_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                validated_at TEXT,
                approved_at TEXT,
                rejected_at TEXT
            )""",
            "CREATE INDEX idx_self_improvement_proposals_status ON self_improvement_proposals(status, updated_at DESC)",
            """CREATE TABLE self_improvement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL REFERENCES self_improvement_proposals(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL CHECK(event_type IN ('proposed','validated','approved_for_pr','rejected')),
                actor TEXT NOT NULL CHECK(actor = 'local_user'),
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX idx_self_improvement_events_proposal ON self_improvement_events(proposal_id, id)",
        ),
    ),
    Migration(
        13,
        "self-improvement-isolated-syntax-check",
        (
            "ALTER TABLE self_improvement_proposals ADD COLUMN static_check_json TEXT",
            "ALTER TABLE self_improvement_proposals ADD COLUMN static_checked_at TEXT",
            "ALTER TABLE self_improvement_events RENAME TO self_improvement_events_v12",
            "DROP INDEX idx_self_improvement_events_proposal",
            """CREATE TABLE self_improvement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL REFERENCES self_improvement_proposals(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL CHECK(event_type IN ('proposed','validated','static_checked','approved_for_pr','rejected')),
                actor TEXT NOT NULL CHECK(actor = 'local_user'),
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """INSERT INTO self_improvement_events(id, proposal_id, event_type, actor, details_json, created_at)
               SELECT id, proposal_id, event_type, actor, details_json, created_at
               FROM self_improvement_events_v12 ORDER BY id""",
            "DROP TABLE self_improvement_events_v12",
            "CREATE INDEX idx_self_improvement_events_proposal ON self_improvement_events(proposal_id, id)",
        ),
    ),

    Migration(
        14,
        "preserve-legacy-research-feedback-data",
        (
            """CREATE TABLE chat_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')),
                correction TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE knowledge_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                license_note TEXT NOT NULL DEFAULT 'Reuse terms not independently reviewed; retain source attribution.',
                status TEXT NOT NULL DEFAULT 'indexed',
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT,
                embedding_model TEXT,
                UNIQUE(source_id, chunk_index)
            )""",
            "CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(content)",
            "CREATE INDEX idx_chat_feedback_rating ON chat_feedback(rating, id DESC)",
            "CREATE INDEX idx_knowledge_chunks_source ON knowledge_chunks(source_id, chunk_index)",
        ),
    ),
    Migration(
        15,
        "durable-task-status-audit-events",
        (
            """CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES task_queue(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL CHECK(event_type IN ('created','status_changed','priority_changed','scheduled')),
                old_status TEXT,
                new_status TEXT NOT NULL,
                old_priority INTEGER,
                new_priority INTEGER NOT NULL,
                old_scheduled_at TEXT,
                new_scheduled_at TEXT NOT NULL,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX idx_task_events_task_id_id ON task_events(task_id, id DESC)",
            """CREATE TRIGGER task_events_after_insert
               AFTER INSERT ON task_queue
               BEGIN
                 INSERT INTO task_events(
                   task_id, event_type, old_status, new_status,
                   old_priority, new_priority, old_scheduled_at, new_scheduled_at
                 ) VALUES (
                   NEW.id, 'created', NULL, NEW.status,
                   NULL, NEW.priority, NULL, NEW.scheduled_at
                 );
               END""",
            """CREATE TRIGGER task_events_after_update
               AFTER UPDATE ON task_queue
               WHEN OLD.status IS NOT NEW.status
                 OR OLD.priority IS NOT NEW.priority
                 OR OLD.scheduled_at IS NOT NEW.scheduled_at
               BEGIN
                 INSERT INTO task_events(
                   task_id, event_type, old_status, new_status,
                   old_priority, new_priority, old_scheduled_at, new_scheduled_at
                 ) VALUES (
                   NEW.id,
                   CASE
                     WHEN OLD.status IS NOT NEW.status THEN 'status_changed'
                     WHEN OLD.priority IS NOT NEW.priority THEN 'priority_changed'
                     ELSE 'scheduled'
                   END,
                   OLD.status, NEW.status, OLD.priority, NEW.priority,
                   OLD.scheduled_at, NEW.scheduled_at
                 );
               END""",
        ),
    ),,

    Migration(
        16,
        "factual-knowledge-core",
        (
            """CREATE TABLE factual_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL DEFAULT 'python' CHECK(domain = 'python'),
                claim TEXT NOT NULL CHECK(length(trim(claim)) > 0),
                normalized_claim TEXT NOT NULL CHECK(length(trim(normalized_claim)) > 0),
                claim_hash TEXT NOT NULL,
                scope_json TEXT NOT NULL DEFAULT '{}',
                environment_name TEXT NOT NULL DEFAULT 'python',
                environment_version TEXT NOT NULL,
                evidence_level TEXT NOT NULL DEFAULT 'L0_raw'
                    CHECK(evidence_level IN ('L0_raw','L1_sourced','L2_tested','L3_human_verified')),
                lifecycle_status TEXT NOT NULL DEFAULT 'unverified'
                    CHECK(lifecycle_status IN ('unverified','active','conflicting','failed','superseded','needs_review')),
                risk_level TEXT NOT NULL DEFAULT 'low'
                    CHECK(risk_level IN ('low','medium','high','critical')),
                human_verified_by TEXT,
                human_verified_at TEXT,
                verified_at TEXT,
                reverify_after TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(claim_hash, environment_name, environment_version),
                CHECK((evidence_level = 'L3_human_verified') = (human_verified_at IS NOT NULL AND human_verified_by IS NOT NULL))
            )""",
            """CREATE TABLE factual_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL REFERENCES factual_knowledge(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL CHECK(source_type IN ('official_docs','source_code','test_output','human_review','other')),
                source_ref TEXT NOT NULL CHECK(length(trim(source_ref)) > 0),
                excerpt TEXT NOT NULL DEFAULT '',
                source_hash TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                applicability_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(knowledge_id, source_ref, source_hash)
            )""",
            """CREATE TABLE factual_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL REFERENCES factual_knowledge(id) ON DELETE CASCADE,
                test_kind TEXT NOT NULL CHECK(test_kind IN ('unit','integration','reproduction','manual')),
                test_name TEXT NOT NULL,
                environment_name TEXT NOT NULL,
                environment_version TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('passed','claim_failed','environment_failure','flaky','inconclusive','not_run')),
                command_hash TEXT,
                result_summary TEXT NOT NULL DEFAULT '',
                log_hash TEXT,
                executed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL REFERENCES factual_knowledge(id) ON DELETE CASCADE,
                related_knowledge_id INTEGER NOT NULL REFERENCES factual_knowledge(id) ON DELETE RESTRICT,
                relation_type TEXT NOT NULL CHECK(relation_type IN ('supersedes','contradicts','depends_on')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(knowledge_id <> related_knowledge_id),
                UNIQUE(knowledge_id, related_knowledge_id, relation_type)
            )""",
            "CREATE INDEX idx_factual_knowledge_status_domain ON factual_knowledge(lifecycle_status, domain)",
            "CREATE INDEX idx_factual_evidence_knowledge ON factual_evidence(knowledge_id, id)",
            "CREATE INDEX idx_factual_tests_knowledge_outcome ON factual_tests(knowledge_id, outcome)",
            """CREATE TRIGGER factual_knowledge_transition_guard
               BEFORE UPDATE OF lifecycle_status ON factual_knowledge
               WHEN NOT (
                 OLD.lifecycle_status = NEW.lifecycle_status OR
                 (OLD.lifecycle_status = 'unverified' AND NEW.lifecycle_status IN ('active','needs_review','failed')) OR
                 (OLD.lifecycle_status = 'active' AND NEW.lifecycle_status IN ('conflicting','failed','superseded','needs_review')) OR
                 (OLD.lifecycle_status = 'needs_review' AND NEW.lifecycle_status IN ('active','conflicting','failed','superseded')) OR
                 (OLD.lifecycle_status = 'conflicting' AND NEW.lifecycle_status IN ('active','failed','superseded','needs_review')) OR
                 (OLD.lifecycle_status = 'failed' AND NEW.lifecycle_status IN ('needs_review','superseded')) OR
                 (OLD.lifecycle_status = 'superseded' AND NEW.lifecycle_status = 'needs_review')
               )
               BEGIN SELECT RAISE(ABORT, 'invalid factual knowledge lifecycle transition'); END""",
            """CREATE TRIGGER factual_knowledge_level_guard
               BEFORE UPDATE OF evidence_level ON factual_knowledge
               WHEN NOT (
                 OLD.evidence_level = NEW.evidence_level OR
                 (OLD.evidence_level = 'L0_raw' AND NEW.evidence_level = 'L1_sourced') OR
                 (OLD.evidence_level = 'L1_sourced' AND NEW.evidence_level = 'L2_tested') OR
                 (OLD.evidence_level = 'L2_tested' AND NEW.evidence_level = 'L3_human_verified')
               )
               BEGIN SELECT RAISE(ABORT, 'invalid factual knowledge evidence-level transition'); END""",
            """CREATE TRIGGER factual_knowledge_source_required
               BEFORE UPDATE OF evidence_level ON factual_knowledge
               WHEN NEW.evidence_level IN ('L1_sourced','L2_tested','L3_human_verified')
                 AND NOT EXISTS (SELECT 1 FROM factual_evidence WHERE knowledge_id = NEW.id)
               BEGIN SELECT RAISE(ABORT, 'evidence level requires traceable evidence'); END""",
            """CREATE TRIGGER factual_knowledge_test_required
               BEFORE UPDATE OF evidence_level ON factual_knowledge
               WHEN NEW.evidence_level IN ('L2_tested','L3_human_verified')
                 AND NOT EXISTS (
                   SELECT 1 FROM factual_tests
                   WHERE knowledge_id = NEW.id AND outcome = 'passed'
                     AND environment_name = NEW.environment_name
                     AND environment_version = NEW.environment_version
                 )
               BEGIN SELECT RAISE(ABORT, 'tested evidence level requires a passing applicable test'); END""",
            """CREATE TRIGGER factual_knowledge_active_guard
               BEFORE UPDATE OF lifecycle_status ON factual_knowledge
               WHEN NEW.lifecycle_status = 'active' AND NEW.evidence_level = 'L0_raw'
               BEGIN SELECT RAISE(ABORT, 'raw knowledge cannot become active'); END""",
            """CREATE TRIGGER factual_knowledge_relation_supersedes_guard
               BEFORE INSERT ON knowledge_relations
               WHEN NEW.relation_type = 'supersedes'
                 AND NOT EXISTS (
                   SELECT 1 FROM factual_knowledge
                   WHERE id = NEW.knowledge_id AND evidence_level <> 'L0_raw'
                     AND lifecycle_status IN ('active','needs_review')
                 )
               BEGIN SELECT RAISE(ABORT, 'only reviewed knowledge may supersede another claim'); END""",
        ),
    ),
)


# This exact fingerprint was captured by the read-only target-host diagnostic on
# 2026-10-18. It is accepted only as a migration source, never as an already
# current schema. The fixture and row-preserving conversion are regression-tested.
LEGACY_LEARNING_SCHEMA_FINGERPRINT = "8932fae6e02d89dfb8230f4dc9becce3cc0e86c53d4b6b90c49652b1c6b9b83b"
_LEGACY_LEARNING_DATA_TABLES = (
    "assessment_attempts",
    "chat_feedback",
    "knowledge_chunks",
    "knowledge_items",
    "knowledge_sources",
    "learning_evidence",
    "learning_experience_versions",
    "learning_experiences",
    "learning_jobs",
    "learning_path_topics",
    "learning_paths",
    "learning_source_text",
    "lesson_reviews",
)
_LEGACY_COPY_ORDER = (
    "learning_paths",
    "learning_path_topics",
    "learning_jobs",
    "learning_experiences",
    "learning_experience_versions",
    "learning_evidence",
    "learning_source_text",
    "knowledge_items",
    "assessment_attempts",
    "lesson_reviews",
    "chat_feedback",
    "knowledge_sources",
    "knowledge_chunks",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_data_fingerprint(connection: sqlite3.Connection, table: str) -> str:
    """Hash one table's ordered values without exposing user data in logs."""
    digest = hashlib.sha256()
    cursor = connection.execute(
        f"SELECT * FROM {_quote_identifier(table)} ORDER BY rowid"
    )
    while True:
        rows = cursor.fetchmany(500)
        if not rows:
            break
        for row in rows:
            canonical = tuple(
                ("bytes", value.hex()) if isinstance(value, bytes)
                else (type(value).__name__, value)
                for value in row
            )
            encoded = repr(canonical).encode("utf-8", errors="surrogatepass")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _migrate_recognized_legacy_learning_database(
    target: Path,
    migrations: tuple[Migration, ...],
    *,
    verified_backup: Path,
) -> int:
    """Convert one reviewed legacy fingerprint using a fresh DB and verified backup."""
    if not verified_backup.is_file() or verified_backup.stat().st_size == 0:
        raise RuntimeError("Verified pre-migration backup is missing; refusing legacy conversion.")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.legacy-migration-", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source_uri = target.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source:
            source.execute("PRAGMA query_only = ON")
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone():
                raise UntrackedSchemaError(
                    "Reviewed legacy fingerprint is only eligible without migration history."
                )
            if schema_fingerprint(source) != LEGACY_LEARNING_SCHEMA_FINGERPRINT:
                raise UntrackedSchemaError(
                    "Legacy learning schema changed since review; refusing conversion."
                )
            table_rows = source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'knowledge_chunks_fts_%' "
                "AND name != 'knowledge_chunks_fts' ORDER BY name"
            ).fetchall()
            actual_tables = {row[0] for row in table_rows}
            if actual_tables != set(_LEGACY_LEARNING_DATA_TABLES):
                raise UntrackedSchemaError(
                    "Reviewed legacy learning table set changed; refusing conversion."
                )
            source_counts = {
                table: int(source.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0])
                for table in _LEGACY_LEARNING_DATA_TABLES
            }

            # Recheck the backup immediately before conversion. This catches a
            # second application process changing rows after the backup was made.
            backup_uri = verified_backup.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(backup_uri, uri=True, timeout=10)) as backup:
                backup.execute("PRAGMA query_only = ON")
                if schema_fingerprint(backup) != LEGACY_LEARNING_SCHEMA_FINGERPRINT:
                    raise RuntimeError("Verified backup no longer matches the reviewed legacy schema.")
                backup_counts = {
                    table: int(backup.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0])
                    for table in _LEGACY_LEARNING_DATA_TABLES
                }
                if backup_counts != source_counts:
                    raise RuntimeError(
                        "Source row counts changed after the pre-migration backup; "
                        "refusing conversion to avoid an inconsistent snapshot."
                    )
                source_digests = {
                    table: _table_data_fingerprint(source, table)
                    for table in _LEGACY_LEARNING_DATA_TABLES
                }
                backup_digests = {
                    table: _table_data_fingerprint(backup, table)
                    for table in _LEGACY_LEARNING_DATA_TABLES
                }
                if backup_digests != source_digests:
                    raise RuntimeError(
                        "Source data changed after the pre-migration backup; "
                        "refusing conversion to avoid migrating an unbacked snapshot."
                    )

            with closing(sqlite3.connect(str(temporary), timeout=10)) as destination:
                destination.execute("PRAGMA foreign_keys = ON")
                destination.execute("PRAGMA busy_timeout = 10000")
                MigrationRunner(migrations).apply(destination)
                destination.execute("BEGIN IMMEDIATE")
                for table in _LEGACY_COPY_ORDER:
                    source_columns = {
                        row[1] for row in source.execute(
                            f"PRAGMA table_info({_quote_identifier(table)})"
                        ).fetchall()
                    }
                    destination_columns = {
                        row[1] for row in destination.execute(
                            f"PRAGMA table_info({_quote_identifier(table)})"
                        ).fetchall()
                    }
                    columns = [
                        row[1] for row in source.execute(
                            f"PRAGMA table_info({_quote_identifier(table)})"
                        ).fetchall()
                        if row[1] in destination_columns
                    ]
                    if not columns or not set(columns) <= source_columns:
                        raise RuntimeError(f"No compatible columns found while migrating {table}.")
                    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
                    select_sql = (
                        f"SELECT {quoted_columns} FROM {_quote_identifier(table)} ORDER BY rowid"
                    )
                    insert_sql = (
                        f"INSERT INTO {_quote_identifier(table)} ({quoted_columns}) "
                        f"VALUES ({', '.join('?' for _ in columns)})"
                    )
                    cursor = source.execute(select_sql)
                    while True:
                        batch = cursor.fetchmany(500)
                        if not batch:
                            break
                        destination.executemany(insert_sql, batch)

                # Preserve AUTOINCREMENT high-water marks, including IDs deleted
                # before migration. Copying only live rows would otherwise allow old IDs
                # to be reused after the migration.
                sequence_rows = source.execute(
                    "SELECT name, seq FROM sqlite_sequence WHERE name IN ("
                    + ", ".join("?" for _ in _LEGACY_COPY_ORDER)
                    + ")",
                    _LEGACY_COPY_ORDER,
                ).fetchall()
                for sequence_name, sequence_value in sequence_rows:
                    current_sequence = destination.execute(
                        "SELECT seq FROM sqlite_sequence WHERE name = ?",
                        (sequence_name,),
                    ).fetchone()
                    if current_sequence is None:
                        destination.execute(
                            "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                            (sequence_name, sequence_value),
                        )
                    elif int(current_sequence[0]) < int(sequence_value):
                        destination.execute(
                            "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                            (sequence_value, sequence_name),
                        )

                # This is a standalone FTS5 table, not an external-content index.
                # Populate it from canonical chunks instead of issuing the external-content
                # 'rebuild' command, which would rebuild from its currently empty content table.
                destination.execute(
                    "INSERT INTO knowledge_chunks_fts(content) "
                    "SELECT content FROM knowledge_chunks ORDER BY id"
                )
                for table, expected_count in source_counts.items():
                    actual_count = int(destination.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                    ).fetchone()[0])
                    if actual_count != expected_count:
                        raise RuntimeError(
                            f"Row-count validation failed for {table}: "
                            f"expected {expected_count}, got {actual_count}."
                        )
                fts_count = int(destination.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks_fts"
                ).fetchone()[0])
                if fts_count != source_counts["knowledge_chunks"]:
                    raise RuntimeError("FTS rebuild row-count validation failed.")
                integrity = destination.execute("PRAGMA integrity_check").fetchall()
                violations = destination.execute("PRAGMA foreign_key_check").fetchall()
                if [row[0] for row in integrity] != ["ok"] or violations:
                    raise RuntimeError(
                        "Migrated learning database failed integrity or foreign-key validation."
                    )
                expected_fingerprint = fingerprint_for_version(
                    migrations, migrations[-1].version
                )
                if schema_fingerprint(destination) != expected_fingerprint:
                    raise RuntimeError("Migrated learning schema does not match the registered current schema.")
                destination.commit()

        # The destination handle is closed above. Close the read-only source
        # connection explicitly as well: Windows may refuse replacement while
        # any handle to the original database remains open.
        source.close()
        os.replace(temporary, target)
        return migrations[-1].version
    except Exception:
        try:
            with closing(sqlite3.connect(str(temporary), timeout=2)) as connection:
                connection.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


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
        "no history was written and no schema changes were applied. "
        f"Observed schema fingerprint: {actual}. "
        "Run the read-only diagnostic from the repository root: "
        "python scripts/inspect_sqlite_schema.py "
        "<database-path> --kind learning --output <report-path>. "
        "Do not reset, manually stamp, or migrate this database until the "
        "schema has been reviewed and a verified backup exists."
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
        source_integrity = source.execute("PRAGMA integrity_check").fetchall()
        source_violations = source.execute("PRAGMA foreign_key_check").fetchall()
        if [row[0] for row in source_integrity] != ["ok"] or source_violations:
            raise RuntimeError(f"Source database failed pre-migration validation: {path}")

        def row_counts(connection: sqlite3.Connection) -> dict[str, int]:
            tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return {
                row[0]: int(connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(row[0])}"
                ).fetchone()[0])
                for row in tables
            }

        source_counts = row_counts(source)
        source_fingerprint = schema_fingerprint(source)
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchall()
        violations = destination.execute("PRAGMA foreign_key_check").fetchall()
        if [row[0] for row in result] != ["ok"] or violations:
            raise RuntimeError(f"Pre-migration backup failed integrity validation: {candidate}")
        if schema_fingerprint(destination) != source_fingerprint:
            raise RuntimeError(f"Pre-migration backup schema verification failed: {candidate}")
        backup_counts = row_counts(destination)
        if backup_counts != source_counts:
            raise RuntimeError(
                f"Pre-migration backup row-count verification failed: {candidate}"
            )
        table_names = [
            row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table in table_names:
            quoted = _quote_identifier(table)
            source_count = int(source.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            backup_count = int(destination.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            if source_count != backup_count:
                raise RuntimeError(
                    f"Pre-migration backup row-count verification failed for table {table}: "
                    f"source={source_count}, backup={backup_count}."
                )
    return candidate


def initialize_database_file(path: str | Path, migrations: Iterable[Migration]) -> int:
    """Back up existing schemas before baseline adoption/upgrades, then initialize safely."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    migration_list = tuple(migrations)
    if not migration_list:
        raise ValueError("At least one migration must be registered.")

    verified_backup: Path | None = None
    if _needs_schema_backup(target, migration_list):
        verified_backup = _create_pre_migration_backup(target)

    if target.is_file() and migration_list == LEARNING_MIGRATIONS:
        with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)) as probe:
            has_history = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone() is not None
            observed_fingerprint = schema_fingerprint(probe)
        if not has_history and observed_fingerprint == LEGACY_LEARNING_SCHEMA_FINGERPRINT:
            if verified_backup is None:
                verified_backup = _create_pre_migration_backup(target)
            try:
                return _migrate_recognized_legacy_learning_database(
                    target, migration_list, verified_backup=verified_backup
                )
            except Exception as exc:
                # Keep startup's fail-closed recovery path actionable without exposing
                # row values or replacing the original file after a failed conversion.
                raise UntrackedSchemaError(
                    "Recognized legacy learning database could not be migrated safely. "
                    f"Original database remains at {target}; verified backup: {verified_backup}. "
                    "No replacement was installed. Review the migration failure before retrying."
                ) from exc

    try:
        with closing(sqlite3.connect(str(target), timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return initialize_connection(connection, migration_list)
    except UntrackedSchemaError as exc:
        details = [
            str(exc),
            f"Database path: {target.resolve()}",
            "The existing database was not migrated or modified.",
        ]
        if verified_backup is not None:
            details.append(f"Verified pre-migration backup: {verified_backup.resolve()}")
        details.append(
            "Inspect the schema and use a reviewed migration for this exact schema; "
            "do not delete the database or manually write migration history."
        )
        raise UntrackedSchemaError(" ".join(details)) from exc



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
