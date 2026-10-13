import sqlite3

import pytest

from app.retrieval_evaluation import indexed_corpus_stats, missing_indexed_relevant_sources


def test_indexed_corpus_stats_counts_only_indexed_sources_and_their_chunks():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE knowledge_sources (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL);
        INSERT INTO knowledge_sources(id, status) VALUES (1, 'indexed'), (2, 'failed');
        INSERT INTO knowledge_chunks(id, source_id) VALUES (1, 1), (2, 1), (3, 2);
        """
    )

    assert indexed_corpus_stats(connection) == {
        "indexed_sources": 1,
        "indexed_chunks": 2,
    }


def test_indexed_corpus_stats_reports_empty_corpus():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE knowledge_sources (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL);
        """
    )

    assert indexed_corpus_stats(connection) == {
        "indexed_sources": 0,
        "indexed_chunks": 0,
    }


def test_missing_relevant_source_is_reported_even_when_unrelated_corpus_is_indexed():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE knowledge_sources (id INTEGER PRIMARY KEY, url TEXT, status TEXT NOT NULL);
        CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL);
        INSERT INTO knowledge_sources(id, url, status)
            VALUES (1, 'https://unrelated.example/doc', 'indexed'),
                   (2, 'https://expected.example/doc', 'failed');
        INSERT INTO knowledge_chunks(id, source_id) VALUES (1, 1), (2, 2);
        """
    )

    assert missing_indexed_relevant_sources(
        connection,
        [{"query": "expected source", "relevant_ids": ["https://expected.example/doc"]}],
    ) == ["https://expected.example/doc"]


def test_missing_relevant_source_accepts_indexed_source_with_chunks():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE knowledge_sources (id INTEGER PRIMARY KEY, url TEXT, status TEXT NOT NULL);
        CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL);
        INSERT INTO knowledge_sources(id, url, status) VALUES (1, 'https://expected.example/doc', 'indexed');
        INSERT INTO knowledge_chunks(id, source_id) VALUES (1, 1);
        """
    )

    assert missing_indexed_relevant_sources(
        connection,
        [{"query": "expected source", "relevant_ids": ["https://expected.example/doc"]}],
    ) == []


def test_missing_indexed_relevant_sources_rejects_invalid_labels():
    connection = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="relevant_ids"):
        missing_indexed_relevant_sources(connection, [{"relevant_ids": [" "]}])
