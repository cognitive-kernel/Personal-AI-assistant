import http.client

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
        "http://@127.0.0.1:11434",
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
        "http://[::1",
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
        "http://localhost:",
        "http://[::1]:",
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


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [
        (400, False),
        (401, False),
        (404, False),
        (408, True),
        (425, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
def test_ollama_http_status_classifies_retryability(
    tmp_path, monkeypatch, status_code, retryable
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            configure_ollama_url(
                tmp_path, monkeypatch, f"http://127.0.0.1:{server.server_port}"
            )
            with pytest.raises(ollama.OllamaError, match=f"HTTP {status_code}") as raised:
                ollama._request("/api/tags", timeout=2)
            assert raised.value.retryable is retryable
        finally:
            server.shutdown()
            thread.join(timeout=2)



def test_factual_review_uses_bounded_timeout_context_and_model_override(monkeypatch):
    captured = {}
    monkeypatch.setattr(ollama, "get_settings", lambda: {"ollama_timeout_seconds": 120})
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {})
    monkeypatch.setattr(ollama, "runtime_limits", lambda *_args, **_kwargs: {"num_ctx": 8192, "cpu_threads": 3})
    monkeypatch.setattr(ollama, "model_keep_alive", lambda *_args, **_kwargs: "0")

    def fake_request(path, payload, *, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"message": {"content": "review result"}}

    monkeypatch.setattr(ollama, "_request", fake_request)
    result = ollama.chat("bounded review input", purpose="factual_review", model_override="reviewer:1")

    assert result == "review result"
    assert captured["payload"]["model"] == "reviewer:1"
    assert captured["timeout"] == 45
    assert captured["payload"]["options"]["num_ctx"] == 4096
    assert captured["payload"]["options"]["num_predict"] == 500


@pytest.mark.parametrize("url", ["http://[::1%25lo0]:11434", "http://[fe80::1%25eth0]:11434"])
def test_ollama_policy_rejects_scoped_ipv6_hosts(tmp_path, monkeypatch, url):
    configure_ollama_url(tmp_path, monkeypatch, url)

    with pytest.raises(ollama.OllamaError, match="Scoped IPv6"):
        ollama._base_url()


@pytest.mark.parametrize(
    "read_error",
    [
        ConnectionResetError("simulated peer disconnect"),
        http.client.IncompleteRead(b"partial", 10),
    ],
)
def test_ollama_request_wraps_response_read_failures(
    tmp_path, monkeypatch, read_error
):
    configure_ollama_url(tmp_path, monkeypatch, "http://127.0.0.1:11434")

    class BrokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            raise read_error

    class BrokenOpener:
        def open(self, *_args, **_kwargs):
            return BrokenResponse()

    monkeypatch.setattr(
        ollama.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: BrokenOpener(),
    )

    with pytest.raises(ollama.OllamaError, match=type(read_error).__name__) as raised:
        ollama._request("/api/tags", timeout=2)

    assert raised.value.retryable is True
    assert str(read_error) not in str(raised.value)
