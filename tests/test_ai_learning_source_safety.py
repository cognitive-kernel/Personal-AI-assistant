from __future__ import annotations

import socket
import urllib.request

import pytest

from app.ai_learning import service
from app.ai_learning.db import connection, initialize_store
from app.pinned_http import PinnedHTTPHandler, PinnedHTTPSHandler


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()


def test_private_source_address_is_rejected_before_http_request(isolated_store, monkeypatch):
    course = service.create_course(title="ایمنی منابع")
    topic = service.add_topic(course["id"], "اعتبارسنجی منابع")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "پیوندها")
    version = service.add_content_version(
        lesson["id"],
        "# پیوندها\n\nبرای بررسی منبع از دامنه عمومی استفاده کنید و به نشانی خصوصی درخواست نفرستید.",
        raw_response="https://example.com",
    )
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO sources(content_version_id,title,url,publisher,source_type,validation_status)
               VALUES(?,?,?,?,?,'unverified')""",
            (version["id"], "Local target", "http://127.0.0.1/private", "127.0.0.1", "test"),
        )
        source_id = int(cursor.lastrowid)

    monkeypatch.setattr(
        service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="خصوصی یا غیرعمومی"):
        service.check_content_source(source_id)


@pytest.mark.parametrize(
    "url,handler_type,expected_ip",
    [
        ("https://example.com/docs", PinnedHTTPSHandler, "93.184.216.34"),
        ("http://example.com/docs", PinnedHTTPHandler, "93.184.216.34"),
    ],
)
def test_source_check_pins_validated_ip_and_disables_proxy(
    isolated_store, monkeypatch, url, handler_type, expected_ip
):
    course = service.create_course(title="Source pinning")
    topic = service.add_topic(course["id"], "Network validation")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Public source")
    version = service.add_content_version(lesson["id"], "# Public source\n\nA source used for network-boundary tests.")
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO sources(content_version_id,title,url,publisher,source_type,validation_status)
               VALUES(?,?,?,?,?,'unverified')""",
            (version["id"], "Public source", url, "example.com", "test"),
        )
        source_id = int(cursor.lastrowid)

    dns_calls = []

    def resolve(*args, **kwargs):
        dns_calls.append((args, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (expected_ip, 443 if url.startswith("https:") else 80))]

    monkeypatch.setattr(service.socket, "getaddrinfo", resolve)
    captured = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(service, "build_opener", build_opener)
    result = service.check_content_source(source_id)

    assert result["validation_status"] == "reviewed"
    assert len(dns_calls) == 1
    assert captured["timeout"] == 8
    pinned = next(handler for handler in captured["handlers"] if isinstance(handler, handler_type))
    assert pinned._resolver(captured["request"].full_url) == expected_ip
    assert any(
        isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in captured["handlers"]
    )
    assert any(
        isinstance(handler, service.HTTPRedirectHandler)
        and handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/")
        is None
        for handler in captured["handlers"]
    )


def test_source_check_rejects_mixed_public_and_private_dns_before_connecting(
    isolated_store, monkeypatch
):
    course = service.create_course(title="Mixed DNS")
    topic = service.add_topic(course["id"], "Network validation")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Public source")
    version = service.add_content_version(lesson["id"], "# Public source\n\nA source used for network-boundary tests.")
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO sources(content_version_id,title,url,publisher,source_type,validation_status)
               VALUES(?,?,?,?,?,'unverified')""",
            (version["id"], "Public source", "https://mixed.example/docs", "mixed.example", "test"),
        )
        source_id = int(cursor.lastrowid)

    monkeypatch.setattr(
        service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443)),
        ],
    )
    monkeypatch.setattr(
        service, "build_opener",
        lambda *_args, **_kwargs: pytest.fail("network opener must not be constructed"),
    )
    with pytest.raises(ValueError, match="خصوصی یا غیرعمومی"):
        service.check_content_source(source_id)


@pytest.mark.parametrize("url", ["https://example.com:8443/docs", "https://example.com:invalid/docs"])
def test_source_check_rejects_nonstandard_or_malformed_ports(isolated_store, url):
    course = service.create_course(title="Invalid source port")
    topic = service.add_topic(course["id"], "Network validation")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "Invalid source")
    version = service.add_content_version(lesson["id"], "# Invalid source\n\nA source used for URL validation tests.")
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO sources(content_version_id,title,url,publisher,source_type,validation_status)
               VALUES(?,?,?,?,?,'unverified')""",
            (version["id"], "Invalid source", url, "example.com", "test"),
        )
        source_id = int(cursor.lastrowid)
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        service.check_content_source(source_id)


def test_source_links_remain_unverified_until_explicit_validation(isolated_store):
    course = service.create_course(title="منابع و انتشار")
    topic = service.add_topic(course["id"], "مدیریت منابع")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "مستندات")
    version = service.add_content_version(
        lesson["id"],
        "# مستندات\n\nبرای جزئیات به [مستندات رسمی](https://example.com/docs) مراجعه کنید. "
        "این متن نمونه برای آزمون ثبت منبع است.",
        raw_response="متن پاسخ",
        source_kind="chatgpt_ui",
    )
    sources = service.list_content_sources(version["id"])
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.com/docs"
    assert sources[0]["validation_status"] == "unverified"


def test_unapproved_topic_cannot_publish_even_reviewed_content(isolated_store):
    course = service.create_course(title="انتشار کنترل‌شده")
    topic = service.add_topic(course["id"], "موضوع تأییدنشده")
    service.decide_topics([topic["id"]], "approved")
    lesson = service.create_lesson(topic["id"], "درس")
    version = service.add_content_version(
        lesson["id"],
        "# درس\n\nاین یک متن محتوایی معتبر برای آزمون است و نباید قبل از تأیید سرفصل منتشر شود.",
    )
    service.review_content(version["id"], "reviewed")
    with connection() as conn:
        conn.execute("UPDATE topics SET approval_status='pending' WHERE id=?", (topic["id"],))
    with pytest.raises(ValueError, match="Topic must be approved"):
        service.publish_content(version["id"])
    assert service.search_knowledge("محتوایی معتبر")["count"] == 0
