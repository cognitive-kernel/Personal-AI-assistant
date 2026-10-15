import pytest

from app import db, ollama


def configure_ollama_url(tmp_path, monkeypatch, url):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    db.update_settings({"ollama_base_url": url})


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "https://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
    ],
)
def test_ollama_policy_allows_loopback_http_endpoints(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    assert ollama._base_url() == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",
        "http://192.168.1.10:11434",
        "http://10.0.0.2:11434",
        "http://0.0.0.0:11434",
    ],
)
def test_ollama_policy_rejects_non_loopback_hosts(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    with pytest.raises(ollama.OllamaError, match="loopback"):
        ollama._base_url()


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@localhost:11434",
        "https://user@127.0.0.1:11434",
    ],
)
def test_ollama_policy_rejects_url_credentials(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    with pytest.raises(ollama.OllamaError, match="Credentials"):
        ollama._base_url()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost:11434",
        "localhost:11434",
        "http:///missing-host",
    ],
)
def test_ollama_policy_rejects_malformed_or_unsupported_urls(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    with pytest.raises(ollama.OllamaError, match="valid HTTP"):
        ollama._base_url()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/api",
        "http://localhost:11434?token=unexpected",
        "http://localhost:11434#fragment",
    ],
)
def test_ollama_policy_rejects_non_base_urls(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    with pytest.raises(ollama.OllamaError, match="base URL"):
        ollama._base_url()
