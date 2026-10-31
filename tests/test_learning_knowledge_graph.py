import hashlib
import json
from types import SimpleNamespace

import pytest

from app import db, learning, learning_knowledge_graph


@pytest.fixture
def graph_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    ids = []
    entries = [
        ("Cache invalidation", "Retained knowledge about cache invalidation and stale reads."),
        ("Transaction visibility", "Retained knowledge about transaction visibility and committed writes."),
    ]
    jobs = [learning.create_job(topic) for topic, _body in entries]
    with db.learning_connection() as connection:
        for (topic, body), job in zip(entries, jobs, strict=True):
            ids.append(int(connection.execute(
                """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
                   VALUES (?, ?, ?, 1, 'retained_after_review', ?)""",
                (topic, body, "web_retrieved", job["id"]),
            ).lastrowid))
    return ids


@pytest.mark.parametrize(
    "source_url",
    [
        "https://@docs.example.test/cache",
        "https://:secret@docs.example.test/cache",
        "https://user:@docs.example.test/cache",
        "https://docs.example.test:/cache",
        "https://docs.example.test:8443/cache",
        "https://docs.example.test:not-a-port/cache",
        "https://[2001:db8::1/cache",
    ],
)
def test_graph_edge_proposal_rejects_invalid_source_authority(graph_store, source_url):
    source_id, target_id = graph_store
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        learning_knowledge_graph.propose_edge(
            source_id,
            target_id,
            relation_type="supports",
            source_url=source_url,
            source_excerpt="The source excerpt is long enough to pass the content bound.",
        )
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_knowledge_graph_edges"
        ).fetchone()[0]
    assert count == 0


def test_graph_edge_proposal_requires_retained_knowledge(graph_store):
    source_id, target_id = graph_store
    edge = learning_knowledge_graph.propose_edge(
        source_id, target_id, relation_type="causes",
        source_url="https://docs.example.test/cache",
        source_excerpt="The source directly describes how stale cache entries can result after writes.",
    )
    assert edge["status"] == "proposed"
    assert edge["source_sha256"] == hashlib.sha256(
        b"The source directly describes how stale cache entries can result after writes."
    ).hexdigest()


def test_graph_edge_requires_two_model_reviews_and_live_source(graph_store, monkeypatch):
    source_id, target_id = graph_store
    excerpt = "The source directly describes how stale cache entries can result after writes."
    edge = learning_knowledge_graph.propose_edge(
        source_id, target_id, relation_type="causes",
        source_url="https://docs.example.test/cache", source_excerpt=excerpt,
    )
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text="Official documentation: " + excerpt, title="Source"),
    )
    monkeypatch.setattr(
        "app.ollama.list_models",
        lambda: [
            {"name": "reviewer-one", "digest": "digest-one"},
            {"name": "reviewer-two", "digest": "digest-two"},
        ],
    )
    monkeypatch.setattr(
        "app.ollama.chat",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "supports",
            "rationale": "The excerpt directly supports this causal relationship rather than mere correlation.",
        }),
    )

    result = learning_knowledge_graph.review_edge(
        edge["id"], expected_source_sha256=edge["source_sha256"]
    )

    assert result["status"] == "accepted"
    context = learning_knowledge_graph.get_graph_context_for_topic("cache invalidation")
    assert context["status"] == "available"
    assert any(item["relation_type"] == "causes" for item in context["edges"])
    assert len(context["nodes"]) == 2


def test_graph_review_stays_proposed_when_only_one_reviewer_is_available(graph_store, monkeypatch):
    source_id, target_id = graph_store
    excerpt = "The source directly describes how stale cache entries can result after writes."
    edge = learning_knowledge_graph.propose_edge(
        source_id, target_id, relation_type="depends_on",
        source_url="https://docs.example.test/cache", source_excerpt=excerpt,
    )
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text=excerpt, title="Source"),
    )
    monkeypatch.setattr("app.ollama.list_models", lambda: [{"name": "only-reviewer", "digest": "one-digest"}])

    result = learning_knowledge_graph.review_edge(
        edge["id"], expected_source_sha256=edge["source_sha256"]
    )
    assert result["status"] == "proposed"
    assert result["review"]["status"] == "inconclusive"
    assert learning_knowledge_graph.get_graph_context_for_topic("cache invalidation")["edges"] == []


def test_graph_edge_rejects_self_relation(graph_store):
    source_id, _ = graph_store
    with pytest.raises(ValueError, match="cannot be related to itself"):
        learning_knowledge_graph.propose_edge(
            source_id, source_id, relation_type="supports",
            source_url="https://docs.example.test/cache",
            source_excerpt="This source is long enough to pass the excerpt size validation.",
        )



def test_accepted_causal_edges_cannot_form_a_cycle(graph_store, monkeypatch):
    source_id, target_id = graph_store
    excerpt = "The source directly describes how stale cache entries can result after writes."
    edge = learning_knowledge_graph.propose_edge(
        source_id, target_id, relation_type="causes",
        source_url="https://docs.example.test/cache", source_excerpt=excerpt,
    )
    monkeypatch.setattr(
        "app.web_sources.fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text=excerpt, title="Source"),
    )
    monkeypatch.setattr(
        "app.ollama.list_models",
        lambda: [
            {"name": "reviewer-one", "digest": "digest-one"},
            {"name": "reviewer-two", "digest": "digest-two"},
        ],
    )
    monkeypatch.setattr(
        "app.ollama.chat",
        lambda *_args, **_kwargs: json.dumps({
            "verdict": "supports",
            "rationale": "The source directly supports the causal relationship in the requested scope.",
        }),
    )
    reviewed = learning_knowledge_graph.review_edge(
        edge["id"], expected_source_sha256=edge["source_sha256"]
    )
    assert reviewed["status"] == "accepted"

    with pytest.raises(ValueError, match="cycle"):
        learning_knowledge_graph.propose_edge(
            target_id, source_id, relation_type="causes",
            source_url="https://docs.example.test/cache",
            source_excerpt="A different source excerpt for the reverse causal edge.",
        )
