"""muelle-client: asks a muelle host to deploy or tear down a site and waits for
the result. Used by the composite action (action.yml); configured through
MUELLE_* environment variables so it can also run outside GitHub Actions.

Required: MUELLE_ENDPOINT, MUELLE_SITE, MUELLE_SECRET
Deploy:   MUELLE_SHA256 and either MUELLE_ARTIFACT_URL or MUELLE_ARTIFACT_ID
          (resolved against the GitHub API of the current repository)
Optional: MUELLE_ACTION (deploy), MUELLE_ENV (preview), MUELLE_BRANCH, MUELLE_SHA,
          MUELLE_TOKEN (sent for the artifact download), MUELLE_TIMEOUT (600),
          MUELLE_POLL_INTERVAL (3), MUELLE_DEPLOYMENTS (true: report GitHub
          Deployment state, authenticated with GH_TOKEN or MUELLE_TOKEN),
          MUELLE_ALLOW_INSECURE (false: allow http:// to non-loopback hosts)

The endpoint must be https://, except for loopback hosts: the body carries
the GITHUB_TOKEN used for the artifact download, and the HMAC signature
authenticates it without hiding it.

TLS uses the system trust store; point SSL_CERT_FILE at a CA bundle to trust
a private CA.

Stdlib only: action.yml runs it on a python set up by actions/setup-python.
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import protocol

HTTP_TIMEOUT = 30


class ClientError(Exception):
    pass


@dataclass(frozen=True)
class GitHub:
    """Context of the GitHub Actions run, empty outside of it."""

    api_url: str
    server_url: str
    repository: str
    run_id: str
    run_attempt: str
    token: str
    output: str
    step_summary: str

    @property
    def run_url(self) -> str:
        return f"{self.server_url}/{self.repository}/actions/runs/{self.run_id}"


@dataclass(frozen=True)
class Settings:
    endpoint: str
    site: str
    secret: str
    action: str
    env: str
    branch: str
    sha: str
    sha256: str
    artifact_url: str
    token: str
    timeout: int
    poll_interval: float
    deployments: bool
    github: GitHub


def _require(env: Mapping[str, str], name: str, hint: str = "") -> str:
    value = env.get(name, "")
    if not value:
        raise ClientError(hint or f"{name} is required")
    return value


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def check_endpoint(endpoint: str, allow_insecure: bool = False) -> None:
    """Refuses endpoints that would send the token in cleartext."""
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme == "https":
        return
    if url.scheme != "http" or not url.hostname:
        raise ClientError(f"MUELLE_ENDPOINT must be an https:// URL: {endpoint}")
    if url.hostname not in LOOPBACK_HOSTS and not allow_insecure:
        raise ClientError(
            f"MUELLE_ENDPOINT must use https:// (got {endpoint}); set MUELLE_ALLOW_INSECURE=true to override"
        )


def settings_from_env(env: Mapping[str, str]) -> Settings:
    """Reads and checks the MUELLE_* / GITHUB_* variables."""
    github = GitHub(
        api_url=env.get("GITHUB_API_URL") or "https://api.github.com",
        server_url=env.get("GITHUB_SERVER_URL") or "https://github.com",
        repository=env.get("GITHUB_REPOSITORY", ""),
        run_id=env.get("GITHUB_RUN_ID", ""),
        run_attempt=env.get("GITHUB_RUN_ATTEMPT") or "1",
        token=env.get("GH_TOKEN") or env.get("MUELLE_TOKEN", ""),
        output=env.get("GITHUB_OUTPUT", ""),
        step_summary=env.get("GITHUB_STEP_SUMMARY", ""),
    )
    endpoint = _require(env, "MUELLE_ENDPOINT").rstrip("/")
    check_endpoint(endpoint, (env.get("MUELLE_ALLOW_INSECURE") or "false") == "true")
    site = _require(env, "MUELLE_SITE")
    secret = _require(env, "MUELLE_SECRET")
    action = env.get("MUELLE_ACTION") or "deploy"
    branch = env.get("MUELLE_BRANCH") or env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME") or ""
    branch = branch.removeprefix("refs/heads/")
    if not branch:
        raise ClientError("cannot determine branch; set MUELLE_BRANCH")
    sha = env.get("MUELLE_SHA") or env.get("GITHUB_SHA", "")

    sha256 = artifact_url = ""
    if action == "deploy":
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ClientError("invalid or missing commit sha")
        sha256 = _require(env, "MUELLE_SHA256", "MUELLE_SHA256 is required for deploy").removeprefix("sha256:")
        artifact_url = env.get("MUELLE_ARTIFACT_URL", "")
        if not artifact_url:
            artifact_id = _require(
                env, "MUELLE_ARTIFACT_ID", "MUELLE_ARTIFACT_ID or MUELLE_ARTIFACT_URL is required for deploy"
            )
            artifact_url = f"{github.api_url}/repos/{github.repository}/actions/artifacts/{artifact_id}/zip"
    elif action != "teardown":
        raise ClientError(f"invalid action '{action}'")

    return Settings(
        endpoint=endpoint,
        site=site,
        secret=secret,
        action=action,
        env=env.get("MUELLE_ENV") or "preview",
        branch=branch,
        sha=sha,
        sha256=sha256,
        artifact_url=artifact_url,
        token=env.get("MUELLE_TOKEN", ""),
        timeout=int(env.get("MUELLE_TIMEOUT") or 600),
        poll_interval=float(env.get("MUELLE_POLL_INTERVAL") or 3),
        deployments=(env.get("MUELLE_DEPLOYMENTS") or "true") == "true",
        github=github,
    )


def request_id(s: Settings, now: int, nonce: str | None = None) -> str:
    """Unique per request: the host accepts each id once."""
    nonce = secrets.token_hex(4) if nonce is None else nonce
    raw = f"{s.github.run_id or 'local'}-{s.github.run_attempt}-{s.site}-{now}-{nonce}"
    return re.sub(r"[^A-Za-z0-9-]", "-", raw)


def build_payload(s: Settings, id: str, now: int) -> dict[str, Any]:
    if s.action == "teardown":
        return protocol.teardown_payload(id=id, site=s.site, branch=s.branch, ts=now)
    return protocol.deploy_payload(
        id=id,
        site=s.site,
        env=s.env,
        branch=s.branch,
        sha=s.sha,
        sha256=s.sha256,
        artifact_url=s.artifact_url,
        token=s.token,
        ts=now,
    )


def github_environment(env: str, branch: str) -> dict[str, Any]:
    """GitHub Deployment fields for a muelle environment."""
    match env:
        case "prod":
            return {"environment": "production", "transient_environment": False, "production_environment": True}
        case "next":
            return {"environment": "next", "transient_environment": False, "production_environment": False}
        case _:
            return {"environment": f"preview/{branch}", "transient_environment": True, "production_environment": False}


def _http(url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None) -> tuple[int, bytes]:
    """Returns (status, body) for any HTTP status; raises on transport errors."""
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


type Http = Callable[..., tuple[int, bytes]]


class Deployment:
    """GitHub Deployment reporting; every call is best effort."""

    def __init__(self, github: GitHub, http: Http = _http) -> None:
        self.github = github
        self.http = http
        self.id: int | None = None

    def _api(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self.github.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }
        url = f"{self.github.api_url}/repos/{self.github.repository}/{path}"
        try:
            code, data = self.http(url, data=json.dumps(body).encode(), headers=headers)
        except OSError as e:
            print(f"::warning::GitHub API {path}: {e}")
            return None
        if code >= 300:
            print(f"::warning::GitHub API {path}: HTTP {code}")
            return None
        return json.loads(data)

    def create(self, sha: str, env: str, branch: str) -> None:
        body = {"ref": sha, "auto_merge": False, "required_contexts": [], **github_environment(env, branch)}
        created = self._api("deployments", body)
        self.id = created.get("id") if created else None

    def set_state(self, state: str, url: str = "") -> None:
        if self.id is None:
            return
        self._api(
            f"deployments/{self.id}/statuses",
            {"state": state, "environment_url": url, "log_url": self.github.run_url},
        )


def wait_for_result(
    endpoint: str,
    id: str,
    timeout: float,
    interval: float,
    http: Http = _http,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, dict[str, Any]]:
    """Polls the status file until success/failure; returns (state, status)."""
    deadline = clock() + timeout
    status: dict[str, Any] = {}
    while True:
        sleep(interval)
        try:
            code, body = http(f"{endpoint}/status/{id}")
            parsed = json.loads(body) if code == 200 else {}
            status = parsed if isinstance(parsed, dict) else {}
        except OSError, ValueError:
            status = {}
        state = str(status.get("state") or "unknown")
        if state in ("success", "failure"):
            return state, status
        if clock() > deadline:
            return "timeout", status


def _append(path: str, line: str) -> None:
    if path:
        with Path(path).open("a") as f:
            f.write(line + "\n")


def run(s: Settings, http: Http = _http, now: Callable[[], float] = time.time, **wait_kw: Any) -> int:
    ts = int(now())
    id = request_id(s, ts)
    deployment = Deployment(s.github, http)
    if s.action == "deploy" and s.deployments:
        deployment.create(s.sha, s.env, s.branch)
        deployment.set_state("in_progress")

    body = protocol.encode(build_payload(s, id, ts))
    headers = {"Content-Type": "application/json", protocol.SIGNATURE_HEADER: protocol.sign(body, s.secret)}
    try:
        code, response = http(f"{s.endpoint}/hooks/{s.site}", data=body, headers=headers)
    except OSError as e:
        code, response = 0, str(e).encode()
    if code != 200:
        print(f"::error::muelle rejected the request ({code}): {response[:500].decode(errors='replace')}")
        deployment.set_state("failure")
        return 1

    state, status = wait_for_result(s.endpoint, id, s.timeout, s.poll_interval, http, **wait_kw)
    url = str(status.get("url") or "")
    message = str(status.get("message") or "")
    _append(s.github.output, f"url={url}")
    print(f"muelle: {state} {message}")
    if state != "success":
        deployment.set_state("failure")
        print(f"::error::muelle {state}: {message}")
        return 1
    deployment.set_state("success", url)
    if url:
        _append(s.github.step_summary, f"Deployed **{s.site}** ({s.env}): {url}")
    return 0


def main() -> int:
    try:
        settings = settings_from_env(os.environ)
    except ClientError as e:
        print(f"::error::{e}")
        return 1
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
