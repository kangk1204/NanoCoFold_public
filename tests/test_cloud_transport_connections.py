"""Persistent Site transport uses local HTTP fixtures and fictitious credentials."""
from __future__ import annotations

import http.client
import hashlib
import io
import json
import threading
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from nanocofold.platform.cloud_agent import Client


CONFIG = {"site_url": "https://fixture.chatgpt.site", "service_token": "fixture-service",
          "agent_token": "fixture-agent", "agent_id": "transport-fixture"}


@pytest.fixture(autouse=True)
def direct_connection(monkeypatch):
    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.getproxies", lambda: {})


@contextmanager
def local_server(monkeypatch):
    records = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            records.append({"client": self.client_address, "method": self.command, "path": self.path,
                            "body": body, "headers": dict(self.headers)})
            self.send_response(200)
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        do_POST = do_GET
        do_PUT = do_GET

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connections = []

    class Connection(http.client.HTTPConnection):
        def __init__(self, host, *, timeout, **_kwargs):
            assert host == "fixture.chatgpt.site"
            super().__init__("127.0.0.1", server.server_port, timeout=timeout)
            connections.append(self)

    monkeypatch.setattr("nanocofold.platform.cloud_agent.http.client.HTTPSConnection", Connection)
    try:
        yield records, connections
    finally:
        for connection in connections:
            connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_json_and_streamed_upload_reuse_the_same_real_socket(tmp_path, monkeypatch):
    with local_server(monkeypatch) as (records, connections):
        client = Client(CONFIG)
        assert client.request("agent/queue") == {"ok": True}
        assert client.request("agent/heartbeat", {"message": "한글 progress"}) == {"ok": True}
        path = tmp_path / "result.bin"
        path.write_bytes(b"result bytes\x00\xff")
        client.upload("results/submission/result.bin", path)
        assert connections[0].sock.gettimeout() == 120
        assert client.request("agent/queue") == {"ok": True}
        assert len(connections) == 1
        assert len({row["client"] for row in records}) == 1
        assert [row["method"] for row in records] == ["GET", "POST", "PUT", "GET"]
        assert json.loads(records[1]["body"])["message"] == "한글 progress"
        assert records[2]["body"] == path.read_bytes()
        assert records[2]["headers"]["Content-Length"] == str(path.stat().st_size)
        assert records[2]["headers"]["X-Content-SHA256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert connections[0].sock.gettimeout() == 30
        assert all(row["headers"]["Authorization"] == "Bearer fixture-agent" for row in records)


class Response:
    def __init__(self, *, status=200, body=b'{"ok":true}', error=None):
        self.status = status
        self.body = body
        self.error = error
        self.reason = "local fixture"
        self.headers = {"Location": "https://other.invalid/must-not-receive-credentials"}
        self.read_sizes = []

    def read(self, size=None):
        self.read_sizes.append(size)
        if self.error:
            raise self.error
        return self.body if size is None else self.body[:size]


def fake_connections(monkeypatch, responses, *, blocked_upload=None):
    connections = []

    class Connection:
        debuglevel = 0
        _http_vsn = 11
        def __init__(self, host, *, timeout):
            self.host = host
            self.timeout = timeout
            self.closed = False
            self.calls = []
            self.owner = threading.get_ident()
            connections.append(self)

        def request(self, method, path, *, body=None, headers):
            self.calls.append((method, path, body, headers))
            if blocked_upload and method == "PUT":
                blocked_upload[0].set()
                assert blocked_upload[1].wait(timeout=3)

        def getresponse(self):
            return responses.pop(0) if responses else Response()

        def close(self):
            self.closed = True

    monkeypatch.setattr("nanocofold.platform.cloud_agent.http.client.HTTPSConnection", Connection)
    return connections


def test_failed_post_read_discards_connection_and_never_replays_in_same_call(monkeypatch):
    connections = fake_connections(monkeypatch, [Response(error=TimeoutError("read interrupted")), Response()])
    client = Client(CONFIG)
    with pytest.raises(TimeoutError, match="interrupted"):
        client.request("agent/status", {"id": "submission", "state": "running"})
    assert len(connections) == 1 and connections[0].closed
    assert len(connections[0].calls) == 1
    assert client.request("agent/queue") == {"ok": True}
    assert len(connections) == 2 and not connections[1].closed
    assert connections[1].calls[0][0] == "GET"


def test_upload_does_not_block_a_separate_heartbeat_connection(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    connections = fake_connections(monkeypatch, [], blocked_upload=(entered, release))
    client = Client(CONFIG)
    source = tmp_path / "result.bin"
    source.write_bytes(b"public local fixture")
    errors = []

    def upload():
        try:
            client.upload("results/submission/result.bin", source)
            client.request("agent/multipart", {"id": "upload-finished"})
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=upload)
    thread.start()
    try:
        assert entered.wait(timeout=3)
        client.request("agent/heartbeat", {"state": "running"})
        client.request("agent/queue")
        assert len(connections) == 2
        assert len({connection.owner for connection in connections}) == 2
        assert len(connections[1].calls) == 2
        assert not release.is_set()
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive() and not errors
    assert len(connections) == 2 and len(connections[0].calls) == 2


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_direct_redirect_is_rejected_without_forwarding_credentials(monkeypatch, status):
    connections = fake_connections(monkeypatch, [Response(status=status)])
    client = Client(CONFIG)
    with pytest.raises(ValueError, match="리다이렉트"):
        client.request("agent/status", {"state": "running"})
    assert len(connections) == 1 and connections[0].closed
    assert connections[0].host == "fixture.chatgpt.site"
    assert len(connections[0].calls) == 1
    assert connections[0].calls[0][1] == "/api/agent/status"


def test_error_body_is_bounded_and_connection_discarded(monkeypatch):
    response = Response(status=503, body=b"x" * 1000000)
    connections = fake_connections(monkeypatch, [response])
    client = Client(CONFIG)
    with pytest.raises(urllib.error.HTTPError) as error:
        client.request("agent/queue")
    assert error.value.code == 503 and len(error.value.read()) == 65536
    assert response.read_sizes == [65536]
    assert connections[0].closed


def test_large_upload_acknowledgement_is_bounded_and_not_reused(tmp_path, monkeypatch):
    response = Response(body=b"x" * 1000000)
    connections = fake_connections(monkeypatch, [response, Response()])
    client = Client(CONFIG)
    source = tmp_path / "result.bin"
    source.write_bytes(b"public fixture")
    client.upload("results/submission/result.bin", source)
    assert response.read_sizes == [65537] and connections[0].closed
    assert client.request("agent/queue") == {"ok": True}
    assert len(connections) == 2


def test_configured_https_proxy_retains_urllib_and_redirect_guard(monkeypatch):
    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.getproxies",
                        lambda: {"https": "http://local-proxy.invalid:8080"})
    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.proxy_bypass", lambda _host: False)
    requests = []

    def open_request(request, *, timeout):
        requests.append((request, timeout))
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.build_opener",
                        lambda *handlers: SimpleNamespace(open=open_request, handlers=handlers))
    monkeypatch.setattr("nanocofold.platform.cloud_agent.http.client.HTTPSConnection",
                        lambda *_args, **_kwargs: pytest.fail("proxy bypassed"))
    client = Client(CONFIG)
    assert client.request("agent/heartbeat", {"state": "running"}) == {"ok": True}
    assert len(requests) == 1 and requests[0][1] == 30
    assert requests[0][0].full_url == "https://fixture.chatgpt.site/api/agent/heartbeat"
    assert requests[0][0].get_method() == "POST"
    assert any(handler.__class__.__name__ == "_NoSiteRedirects" for handler in client.opener.handlers)


def test_proxy_bypass_uses_persistent_direct_connection(monkeypatch):
    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.getproxies",
                        lambda: {"https": "http://local-proxy.invalid:8080"})
    monkeypatch.setattr("nanocofold.platform.cloud_agent.urllib.request.proxy_bypass", lambda _host: True)
    connections = fake_connections(monkeypatch, [])
    client = Client(CONFIG)
    assert client.request("agent/queue") == {"ok": True}
    assert client.request("agent/heartbeat", {"state": "running"}) == {"ok": True}
    assert len(connections) == 1

