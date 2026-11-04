import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from muelle.download import DownloadError, _NoDowngrade, download, sha256_file


class Server:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.auth: dict[str, str | None] = {}
        self.hits: dict[str, int] = {}
        self.url = ""


@pytest.fixture
def server() -> Iterator[Server]:
    state = Server()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            state.auth[self.path] = self.headers.get("Authorization")
            state.hits[self.path] = state.hits.get(self.path, 0) + 1
            code, headers, body = state.routes.get(self.path, (404, {}, b""))
            self.send_response(code)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{httpd.server_port}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield state
    httpd.shutdown()


def test_download_sends_token_but_not_across_redirects(server: Server, tmp_path: Path) -> None:
    server.routes["/zip"] = (302, {"Location": "/blob"}, b"")
    server.routes["/blob"] = (200, {}, b"artifact")
    dest = tmp_path / "a"
    download(f"{server.url}/zip", dest, token="ghs_x", max_bytes=100)
    assert dest.read_bytes() == b"artifact"
    assert server.auth["/zip"] == "Bearer ghs_x"
    assert server.auth["/blob"] is None


def test_download_enforces_max_bytes(server: Server, tmp_path: Path) -> None:
    server.routes["/big"] = (200, {}, b"x" * 1000)
    with pytest.raises(DownloadError, match="larger than 10 bytes"):
        download(f"{server.url}/big", tmp_path / "a", token="", max_bytes=10)


def test_download_does_not_retry_client_errors(server: Server, tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="HTTP 404"):
        download(f"{server.url}/missing", tmp_path / "a", token="", max_bytes=10)
    assert server.hits["/missing"] == 1


def test_download_retries_server_errors(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("muelle.download.time.sleep", lambda _s: None)
    server.routes["/flaky"] = (503, {}, b"")
    with pytest.raises(DownloadError, match="HTTP 503"):
        download(f"{server.url}/flaky", tmp_path / "a", token="", max_bytes=10, retries=2)
    assert server.hits["/flaky"] == 3


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_redirect_from_https_to_http_is_refused() -> None:
    request = urllib.request.Request("https://api.github.com/x")
    with pytest.raises(DownloadError, match="refusing redirect from https to http"):
        _NoDowngrade().redirect_request(request, None, 302, "Found", {}, "http://evil.test/x")
    allowed = _NoDowngrade().redirect_request(request, None, 302, "Found", {}, "https://blob.test/x")
    assert allowed is not None
    assert allowed.full_url == "https://blob.test/x"
