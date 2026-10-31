"""Regression tests for strict integer request-model contracts.

These tests exercise Pydantic's HTTP-boundary models directly so coercion is
rejected before route handlers can perform persistence or queue side effects.
"""
import pytest
from pydantic import ValidationError

from app.ai_learning.routes import (
    BrowserLessonStart,
    BrowserOutlineStart,
    LearningResume,
    QueryFeedback,
    RunCreate,
    RunHistoryDelete,
    TopicCreate,
    TopicDecision,
    TopicEdit,
    TopicOrder,
)
from app.experience_routes import (
    ExperienceTestRunRequest,
    HypothesisProposalRequest,
    RetainedKnowledgeHypothesisRequest,
)
from app.knowledge_base_routes import SourceSearchRequest
from app.learning_candidate_routes import CandidateTestPlanItem
from app.learning_knowledge_graph_routes import EdgeProposalRequest
from app.learning_source_sync_routes import SourceSyncCheckRequest
from app.storage_routes import BackupPruneRequest, RestoreStagingPruneRequest


_CASES = [
    (BrowserOutlineStart, {"request_key": "strict"}, "course_id", 1),
    (RunCreate, {"run_type": "outline", "request_key": "strict"}, "course_id", 1),
    (RunCreate, {"course_id": 1, "run_type": "lesson", "request_key": "strict"}, "topic_ids", [1]),
    (LearningResume, {}, "course_id", 1),
    (BrowserLessonStart, {"topic_ids": [1], "request_key": "strict"}, "course_id", 1),
    (BrowserLessonStart, {"course_id": 1, "request_key": "strict"}, "topic_ids", [1]),
    (RunHistoryDelete, {}, "run_ids", [1]),
    (TopicCreate, {"title": "topic"}, "parent_topic_id", 1),
    (TopicEdit, {}, "parent_topic_id", 1),
    (TopicOrder, {}, "topic_ids", [1]),
    (TopicDecision, {"decision": "approve"}, "topic_ids", [1]),
    (QueryFeedback, {"rating": 1}, "query_trace_id", 1),
    (HypothesisProposalRequest, {"topic": "topic", "problem": "problem"}, "max_candidates", 1),
    (RetainedKnowledgeHypothesisRequest, {"problem": "problem", "max_candidates": 1}, "knowledge_id", 1),
    (RetainedKnowledgeHypothesisRequest, {"knowledge_id": 1, "problem": "problem"}, "max_candidates", 1),
    (
        ExperienceTestRunRequest,
        {"confirm": True, "test_name": "test", "source_code": "print(1)"},
        "timeout_seconds",
        1,
    ),
    (
        ExperienceTestRunRequest,
        {"confirm": True, "test_name": "test", "source_code": "print(1)"},
        "max_output_bytes",
        1,
    ),
    (SourceSearchRequest, {"query": "query"}, "limit", 1),
    (SourceSyncCheckRequest, {}, "limit", 1),
    (
        EdgeProposalRequest,
        {
            "target_knowledge_id": 1,
            "relation_type": "supports",
            "source_url": "https://example.org/source",
            "source_excerpt": "A sufficiently long source excerpt for validation.",
        },
        "source_knowledge_id",
        1,
    ),
    (
        EdgeProposalRequest,
        {
            "source_knowledge_id": 1,
            "relation_type": "supports",
            "source_url": "https://example.org/source",
            "source_excerpt": "A sufficiently long source excerpt for validation.",
        },
        "target_knowledge_id",
        1,
    ),
    (
        CandidateTestPlanItem,
        {"name": "test", "objective": "A sufficiently detailed objective.", "expected_result": "A sufficiently detailed expected result."},
        "oracle_version",
        1,
    ),
    (BackupPruneRequest, {"confirm": False}, "keep_last", 3),
    (BackupPruneRequest, {"confirm": False}, "min_age_days", 30),
    (RestoreStagingPruneRequest, {"confirm": False}, "keep_last", 1),
    (RestoreStagingPruneRequest, {"confirm": False}, "min_age_days", 7),
]


@pytest.mark.parametrize(("model", "base", "field", "valid_value"), _CASES)
@pytest.mark.parametrize("invalid_value", [True, 1.5, "2"])
def test_integer_request_fields_reject_coercion(model, base, field, valid_value, invalid_value):
    payload = {**base, field: invalid_value}
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(("model", "base", "field", "valid_value"), _CASES)
def test_integer_request_fields_accept_valid_integers(model, base, field, valid_value):
    payload = {**base, field: valid_value}
    validated = model.model_validate(payload)
    assert getattr(validated, field) == valid_value
