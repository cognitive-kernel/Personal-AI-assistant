import pytest

from app import source_library


def test_source_url_policy_rejects_non_https_and_unapproved_hosts():
    with pytest.raises(source_library.SourceError):
        source_library._validate_url("http://learn.microsoft.com/en-us/sql/")
    with pytest.raises(source_library.SourceError):
        source_library._validate_url("https://example.com/article")
    with pytest.raises(source_library.SourceError):
        source_library._validate_url("https://127.0.0.1/private")


def test_chunks_are_bounded_and_keep_overlapping_context():
    text = ("SQL Server query optimization and execution plans. " * 150)
    chunks = source_library._chunks(text)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= source_library.CHUNK_CHARS for chunk in chunks)
    assert any(set(chunks[i].split()[-10:]) & set(chunks[i + 1].split()[:10]) for i in range(len(chunks) - 1))


def test_terms_normalize_common_persian_variants():
    assert "یادگیری" in source_library._terms("يادگيری")
    assert "کد" in source_library._terms("كد")
