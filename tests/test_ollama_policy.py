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


def test_ollama_request_does_not_follow_redirects_to_non_loopback(
    tmp_path, monkeypatch
):
    import json
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    external_attempts = []
    original_create_connection = socket.create_connection

    def block_external_connection(address, *args, **kwargs):
        if address[0] == "example.com":
            external_attempts.append(address)
            raise AssertionError("redirect attempted a non-loopback connection")
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", block_external_connection)
    # Prevent environment-configured proxies from bypassing the destination guard.
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(307)
            self.send_header("Location", "http://example.com/redirect-target")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler) as redirect_server:
        redirect_thread = threading.Thread(
            target=redirect_server.serve_forever, daemon=True
        )
        redirect_thread.start()
        try:
            configure_ollama_url(
                tmp_path,
                monkeypatch,
                f"http://127.0.0.1:{redirect_server.server_port}",
            )
            with pytest.raises(ollama.OllamaError, match="request failed"):
                ollama._request("/api/tags", timeout=2)
            assert external_attempts == []
        finally:
            redirect_server.shutdown()
            redirect_thread.join(timeout=2)
