import sqlite3

from app.retrieval_evaluation import indexed_corpus_stats


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
