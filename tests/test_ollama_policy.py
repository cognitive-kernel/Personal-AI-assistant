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

    expected = url.replace('localhost', '127.0.0.1') if 'localhost' in url else url
    assert ollama._base_url() == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",
        "http://192.168.1.10:11434",
        "http://10.0.0.2:11434",
        "http://0.0.0.0:11434",
        "http://localhost.evil:11434",
        "http://localhost.:11434",
        "http://localhost.localdomain:11434",
        "http://127.0.0.1.nip.io:11434",
        "http://2130706433:11434",
        "http://0177.0.0.1:11434",
        "http://[::ffff:127.0.0.1]:11434",
        "http://[::ffff:192.168.1.10]:11434",
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
    import socket
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    external_attempts = []
    original_create_connection = socket.create_connection

    def block_external_connection(address, *args, **kwargs):
        if address[0] == "example.com":
            external_attempts.append(address)
            raise AssertionError("redirect attempted a non-loopback connection")
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", block_external_connection)
    # Do not let environment-configured proxies hide the final destination from the guard.
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

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



def test_ollama_request_ignores_environment_configured_proxies(
    tmp_path, monkeypatch
):
    import json
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    target_requests = []
    proxy_requests = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(self.path)
            body = json.dumps({"models": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_requests.append(self.path)
            body = b'{"proxied": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler) as target, ThreadingHTTPServer(
        ("127.0.0.1", 0), ProxyHandler
    ) as proxy:
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (target, proxy)
        ]
        for thread in threads:
            thread.start()
        try:
            monkeypatch.setattr(
                urllib.request,
                "getproxies",
                lambda: {"http": f"http://127.0.0.1:{proxy.server_port}"},
            )
            monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
            configure_ollama_url(
                tmp_path,
                monkeypatch,
                f"http://127.0.0.1:{target.server_port}",
            )
            assert ollama._request("/api/tags") == {"models": []}
            assert target_requests == ["/api/tags"]
            assert proxy_requests == []
        finally:
            for server in (target, proxy):
                server.shutdown()
            for thread in threads:
                thread.join(timeout=2)


def test_ollama_request_rejects_oversized_response(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class LargeResponseHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"models":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    monkeypatch.setattr(ollama, "_MAX_RESPONSE_BYTES", 8)
    with ThreadingHTTPServer(("127.0.0.1", 0), LargeResponseHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            configure_ollama_url(
                tmp_path,
                monkeypatch,
                f"http://127.0.0.1:{server.server_port}",
            )
            with pytest.raises(ollama.OllamaError, match="exceeded the 8 MiB limit"):
                ollama._request("/api/tags", timeout=2)
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:abc",
        "http://localhost:0",
        "http://localhost:65536",
    ],
)
def test_ollama_policy_rejects_invalid_ports(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)
    with pytest.raises(ollama.OllamaError, match="port"):
        ollama._base_url()


def test_ollama_request_wraps_invalid_utf8_response(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class InvalidUtf8Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"\xff")

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), InvalidUtf8Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            configure_ollama_url(
                tmp_path, monkeypatch, f"http://127.0.0.1:{server.server_port}"
            )
            with pytest.raises(ollama.OllamaError, match="request failed"):
                ollama._request("/api/tags", timeout=2)
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_ollama_model_inventory_exposes_manifest_digest(tmp_path, monkeypatch):
    configure_ollama_url(tmp_path, monkeypatch, "http://127.0.0.1:11434")
    digest = "sha256:" + "a" * 64
    monkeypatch.setattr(ollama, "_request", lambda path, **_kwargs: {
        "models": [{
            "name": "test-model:latest",
            "size": 1234,
            "modified_at": "2026-10-16T00:00:00Z",
            "digest": digest,
        }]
    })
    assert ollama.list_models() == [{
        "name": "test-model:latest",
        "size": 1234,
        "modified_at": "2026-10-16T00:00:00Z",
        "digest": digest,
    }]


def test_ollama_model_inventory_handles_missing_or_invalid_digest(tmp_path, monkeypatch):
    configure_ollama_url(tmp_path, monkeypatch, "http://127.0.0.1:11434")
    monkeypatch.setattr(ollama, "_request", lambda path, **_kwargs: {
        "models": [{"name": "test-model:latest", "digest": 42}]
    })
    assert ollama.list_models()[0]["digest"] is None

def test_ollama_serializes_inference_requests_and_checks_headroom(
    tmp_path, monkeypatch
):
    import json
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    active = 0
    max_active = 0
    counts_lock = threading.Lock()
    headroom_checks = []

    class InferenceHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal active, max_active
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with counts_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            body = json.dumps({"message": {"content": "ok"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with counts_lock:
                active -= 1

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), InferenceHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            configure_ollama_url(
                tmp_path, monkeypatch, f"http://127.0.0.1:{server.server_port}"
            )
            monkeypatch.setattr(
                ollama, "_ensure_resource_headroom",
                lambda: headroom_checks.append(threading.get_ident()),
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _: ollama._request(
                        "/api/chat", {"model": "test", "messages": []}, timeout=2
                    ),
                    range(2),
                ))
            assert len(results) == 2
            assert max_active == 1
            assert len(headroom_checks) == 2
        finally:
            server.shutdown()
            thread.join(timeout=2)



def test_ollama_error_exposes_retryability_without_changing_exception_type():
    retryable = ollama.OllamaError("connection timed out", retryable=True)
    permanent = ollama.OllamaError("invalid endpoint")

    assert isinstance(retryable, RuntimeError)
    assert retryable.retryable is True
    assert permanent.retryable is False
