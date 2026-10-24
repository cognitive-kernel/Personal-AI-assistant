import urllib.request

from app import pinned_http


def test_http_connection_uses_the_prevalidated_ip(monkeypatch):
    captured = {}

    class FakeSocket:
        def close(self):
            captured["closed"] = True

    def fake_create_connection(address, timeout, source_address):
        captured.update(address=address, timeout=timeout, source_address=source_address)
        return FakeSocket()

    monkeypatch.setattr(pinned_http.socket, "create_connection", fake_create_connection)
    connection = pinned_http._PinnedHTTPConnection(
        "docs.example", pinned_ip="93.184.216.34", timeout=4
    )
    connection.connect()

    assert captured["address"] == ("93.184.216.34", 80)
    assert captured["timeout"] == 4
    assert connection.sock is not None
    connection.close()
    assert captured["closed"] is True


def test_https_connection_pins_tcp_ip_but_keeps_hostname_for_tls(monkeypatch):
    captured = {}

    class FakeSocket:
        def close(self):
            captured["closed"] = True

    class FakeContext:
        check_hostname = True
        def wrap_socket(self, sock, *, server_hostname):
            captured["server_hostname"] = server_hostname
            captured["wrapped_socket"] = sock
            return sock

    raw_socket = FakeSocket()

    def fake_create_connection(address, timeout, source_address):
        captured.update(address=address, timeout=timeout, source_address=source_address)
        return raw_socket

    monkeypatch.setattr(pinned_http.socket, "create_connection", fake_create_connection)
    connection = pinned_http._PinnedHTTPSConnection(
        "docs.example", pinned_ip="93.184.216.34", timeout=5,
        context=FakeContext(), check_hostname=True,
    )
    connection.connect()

    assert captured["address"] == ("93.184.216.34", 443)
    assert captured["server_hostname"] == "docs.example"
    assert captured["wrapped_socket"] is raw_socket
    connection.close()
    assert captured["closed"] is True


def test_pinned_https_handler_resolves_each_request_url(monkeypatch):
    captured = {}

    def resolver(url):
        captured["url"] = url
        return "93.184.216.34"

    handler = pinned_http.PinnedHTTPSHandler(resolver)

    def fake_do_open(factory, req, **kwargs):
        captured["request_url"] = req.full_url
        captured["connection"] = factory("docs.example", **kwargs)
        return "opened"

    monkeypatch.setattr(handler, "do_open", fake_do_open)
    request = urllib.request.Request("https://docs.example/page")

    assert handler.https_open(request) == "opened"
    assert captured["url"] == "https://docs.example/page"
    assert captured["connection"]._pinned_ip == "93.184.216.34"
