"""HTTP transport that connects to a validated IP while preserving the URL hostname for TLS.

Resolvers must validate URL policy and return one approved IP address. Redirects are
handled by urllib handlers, so each redirected URL is independently resolved and pinned.
"""
from __future__ import annotations

import http.client
import socket
import urllib.request
from collections.abc import Callable


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(host, **kwargs)

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            if self._tunnel_host:
                self.sock = sock
                self._tunnel()
                sock = self.sock
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    """HTTP handler that pins each request to the IP returned by a policy resolver."""

    def __init__(self, resolver: Callable[[str], str], debuglevel: int = 0):
        super().__init__(debuglevel=debuglevel)
        self._resolver = resolver

    def http_open(self, req):
        pinned_ip = self._resolver(req.full_url)
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPConnection(
                host, pinned_ip=pinned_ip, **kwargs
            ),
            req,
        )


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPS handler that pins TCP destination while retaining TLS hostname checks."""

    def __init__(self, resolver: Callable[[str], str], debuglevel: int = 0):
        super().__init__(debuglevel=debuglevel)
        self._resolver = resolver

    def https_open(self, req):
        pinned_ip = self._resolver(req.full_url)
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(
                host, pinned_ip=pinned_ip, **kwargs
            ),
            req,
            context=self._context,
            check_hostname=self._check_hostname,
        )
