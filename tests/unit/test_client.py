import hmac
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from helpers import make_zip, sha256

from muelle import client, protocol, receive, worker
from muelle.client import ClientError, Settings, settings_from_env
from muelle.config import Config

SECRET = "deadbeef"
SHA = "3" * 40

BASE_ENV = {
    "MUELLE_ENDPOINT": "https://muelle.test/",
    "MUELLE_SITE": "demo",
    "MUELLE_SECRET": SECRET,
    "MUELLE_BRANCH": "refs/heads/feat/x",
    "MUELLE_SHA": SHA,
    "MUELLE_SHA256": "sha256:" + "0" * 64,
    "MUELLE_ARTIFACT_URL": "https://api.github.com/repos/owner/demo/a.zip",
}


def test_settings_defaults() -> None:
    s = settings_from_env(BASE_ENV)
    assert s.endpoint == "https://muelle.test"
    assert s.action == "deploy"
    assert s.env == "preview"
    assert s.branch == "feat/x"
    assert s.sha256 == "0" * 64
    assert s.timeout == 600
    assert s.deployments is True


def test_settings_branch_falls_back_to_github_refs() -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != "MUELLE_BRANCH"}
    assert settings_from_env({**env, "GITHUB_REF_NAME": "main"}).branch == "main"
    assert settings_from_env({**env, "GITHUB_HEAD_REF": "pr", "GITHUB_REF_NAME": "1/merge"}).branch == "pr"
    with pytest.raises(ClientError, match="cannot determine branch"):
        settings_from_env(env)


def test_settings_resolves_artifact_id() -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != "MUELLE_ARTIFACT_URL"}
    s = settings_from_env({**env, "MUELLE_ARTIFACT_ID": "42", "GITHUB_REPOSITORY": "owner/demo"})
    assert s.artifact_url == "https://api.github.com/repos/owner/demo/actions/artifacts/42/zip"
    with pytest.raises(ClientError, match="MUELLE_ARTIFACT_ID or MUELLE_ARTIFACT_URL"):
        settings_from_env(env)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"MUELLE_SECRET": ""}, "MUELLE_SECRET is required"),
        ({"MUELLE_SHA": "abc"}, "invalid or missing commit sha"),
        ({"MUELLE_SHA256": ""}, "MUELLE_SHA256 is required"),
        ({"MUELLE_ACTION": "explode"}, "invalid action 'explode'"),
    ],
)
def test_settings_errors(override: dict[str, str], message: str) -> None:
    with pytest.raises(ClientError, match=message):
        settings_from_env({**BASE_ENV, **override})


def test_teardown_needs_no_artifact() -> None:
    s = settings_from_env({"MUELLE_ENDPOINT": "x", "MUELLE_SITE": "demo", "MUELLE_SECRET": "s",
                           "MUELLE_ACTION": "teardown", "MUELLE_BRANCH": "b"})  # fmt: skip
    payload = client.build_payload(s, "id-000001", 5)
    assert payload == protocol.teardown_payload(id="id-000001", site="demo", branch="b", ts=5)


def test_request_id_is_sanitized() -> None:
    s = settings_from_env({**BASE_ENV, "MUELLE_SITE": "my_site", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "2"})
    assert client.request_id(s, 100, "ab") == "7-2-my-site-100-ab"
    assert client.request_id(settings_from_env(BASE_ENV), 100, "ab") == "local-1-demo-100-ab"
    assert client.request_id(s, 100) != client.request_id(s, 100)


@pytest.mark.parametrize(
    ("env", "name", "transient", "production"),
    [("prod", "production", False, True), ("next", "next", False, False), ("preview", "preview/b", True, False)],
)
def test_github_environment(env: str, name: str, transient: bool, production: bool) -> None:
    assert client.github_environment(env, "b") == {
        "environment": name,
        "transient_environment": transient,
        "production_environment": production,
    }


def test_wait_for_result_times_out() -> None:
    clock = iter(range(100))
    state, _ = client.wait_for_result(
        "https://x",
        "id",
        timeout=3,
        interval=0,
        http=lambda _url: (200, b'{"state":"in_progress"}'),
        clock=lambda: next(clock),
        sleep=lambda _s: None,
    )
    assert state == "timeout"


def test_wait_for_result_survives_garbage() -> None:
    replies = iter([(502, b"bad gateway"), (200, b"[]"), (200, b"{"), (200, b'{"state":"failure","message":"m"}')])
    state, status = client.wait_for_result(
        "https://x", "id", timeout=60, interval=0, http=lambda _url: next(replies), sleep=lambda _s: None
    )
    assert (state, status["message"]) == ("failure", "m")


class FakeGitHubHttp:
    """Records GitHub API calls and answers muelle calls from a script."""

    def __init__(self, hook: tuple[int, bytes], statuses: list[dict[str, Any]]) -> None:
        self.hook = hook
        self.statuses = iter(statuses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __call__(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None):
        self.calls.append((url, json.loads(data) if data else None))
        if "/deployments/" in url:
            return 201, b"{}"
        if url.endswith("/deployments"):
            return 201, b'{"id": 9}'
        if "/hooks/" in url:
            return self.hook
        return 200, json.dumps(next(self.statuses)).encode()


def github_settings(tmp_path: Path, **env: str) -> Settings:
    return settings_from_env(
        {
            **BASE_ENV,
            "MUELLE_POLL_INTERVAL": "0",
            "GITHUB_REPOSITORY": "owner/demo",
            "GITHUB_RUN_ID": "7",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            **env,
        }
    )


def test_run_reports_github_deployment(tmp_path: Path) -> None:
    http = FakeGitHubHttp((200, b"{}"), [{"state": "queued"}, {"state": "success", "url": "https://u"}])
    assert client.run(github_settings(tmp_path), http=http, sleep=lambda _s: None) == 0
    urls = [url.removeprefix("https://api.github.com/repos/owner/demo/") for url, _ in http.calls]
    assert urls[0] == "deployments"
    assert urls[1] == "deployments/9/statuses"
    assert http.calls[0][1] is not None
    assert http.calls[0][1]["environment"] == "preview/feat/x"
    states = [body["state"] for url, body in http.calls if "/statuses" in url and body]
    assert states == ["in_progress", "success"]
    assert (tmp_path / "output").read_text() == "url=https://u\n"
    assert "https://u" in (tmp_path / "summary").read_text()


def test_run_fails_on_rejection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    http = FakeGitHubHttp((500, b"muelle: invalid sha"), [])
    assert client.run(github_settings(tmp_path), http=http) == 1
    assert "::error::muelle rejected the request (500): muelle: invalid sha" in capsys.readouterr().out
    states = [body["state"] for url, body in http.calls if "/statuses" in url and body]
    assert states == ["in_progress", "failure"]


def test_run_without_deployments_skips_github(tmp_path: Path) -> None:
    http = FakeGitHubHttp((200, b"{}"), [{"state": "failure", "message": "boom"}])
    settings = github_settings(tmp_path, MUELLE_DEPLOYMENTS="false")
    assert client.run(settings, http=http, sleep=lambda _s: None) == 1
    assert not any("api.github.com" in url for url, _ in http.calls)


# End to end over real HTTP: client -> fake host (HMAC check like webhook,
# then muelle.receive) -> muelle.worker, with status served from disk.


@pytest.fixture
def host(config: Config) -> Iterator[tuple[str, dict[str, bytes]]]:
    artifacts: dict[str, bytes] = {}

    def fetch(url: str, dest: Path, *, token: str, max_bytes: int) -> None:
        dest.write_bytes(artifacts[url])

    def spawn(cfg: Config, id: str) -> None:
        threading.Thread(target=worker.process_spooled, args=(cfg, id, fetch), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            site = self.path.removeprefix("/hooks/")
            signature = self.headers.get(protocol.SIGNATURE_HEADER, "")
            if not hmac.compare_digest(signature, protocol.sign(body, SECRET)):
                return self._reply(500, b"Error occurred while evaluating hook rules.")
            try:
                response = receive.handle(body.decode(), site, config, int(time.time()), spawn=spawn)
            except receive.Rejected as e:
                return self._reply(500, f"muelle: {e}".encode())
            self._reply(200, json.dumps(response).encode())

        def do_GET(self) -> None:
            path = config.state_dir / "status" / f"{self.path.removeprefix('/status/')}.json"
            if not path.exists():
                return self._reply(404, b"")
            self._reply(200, path.read_bytes())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", artifacts
    server.shutdown()


def test_end_to_end_deploy_and_teardown(config: Config, host: tuple[str, dict[str, bytes]], tmp_path: Path) -> None:
    endpoint, artifacts = host
    data = make_zip({"index.html": "client-preview"})
    artifacts[BASE_ENV["MUELLE_ARTIFACT_URL"]] = data
    env = {
        **BASE_ENV,
        "MUELLE_ENDPOINT": endpoint,
        "MUELLE_SHA256": f"sha256:{sha256(data)}",
        "MUELLE_DEPLOYMENTS": "false",
        "MUELLE_POLL_INTERVAL": "0.05",
        "MUELLE_TIMEOUT": "10",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    assert client.run(settings_from_env(env)) == 0
    url = (tmp_path / "output").read_text().strip().removeprefix("url=")
    slot = protocol.slot_for("demo", "preview", "feat/x", SHA)
    assert url == f"https://{slot}-demo.muelle.test"
    assert (config.base_dir / "demo" / slot / "current" / "index.html").read_text() == "client-preview"

    assert client.run(settings_from_env({**env, "MUELLE_ACTION": "teardown"})) == 0
    assert not (config.base_dir / "demo" / slot).exists()


def test_end_to_end_wrong_secret(host: tuple[str, dict[str, bytes]], capsys: pytest.CaptureFixture[str]) -> None:
    endpoint, _ = host
    env = {**BASE_ENV, "MUELLE_ENDPOINT": endpoint, "MUELLE_SECRET": "wrong", "MUELLE_DEPLOYMENTS": "false"}
    assert client.run(settings_from_env(env)) == 1
    assert "hook rules" in capsys.readouterr().out


def test_end_to_end_failed_deploy(
    config: Config, host: tuple[str, dict[str, bytes]], capsys: pytest.CaptureFixture[str]
) -> None:
    endpoint, artifacts = host
    artifacts[BASE_ENV["MUELLE_ARTIFACT_URL"]] = b"junk"
    env = {**BASE_ENV, "MUELLE_ENDPOINT": endpoint, "MUELLE_DEPLOYMENTS": "false", "MUELLE_POLL_INTERVAL": "0.05"}
    assert client.run(settings_from_env(env)) == 1
    assert "::error::muelle failure: failed: sha256 mismatch" in capsys.readouterr().out
