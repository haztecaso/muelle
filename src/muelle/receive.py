"""muelle-receive SITE

Run by webhook after the HMAC rule has passed, with the request body in
$MUELLE_PAYLOAD. Validates it against the Nix-declared site table, queues it in
the spool dir and detaches the worker. Stdout is the HTTP response body; a
non-zero exit makes webhook answer 500 with the stderr message.
"""

import json
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from . import index, protocol
from .config import Config
from .state import StateDir


class Rejected(Exception):
    pass


_ID = re.compile(r"[A-Za-z0-9-]{8,96}")
_TS = re.compile(r"[0-9]{9,11}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_URL = re.compile(r"[A-Za-z0-9._/:?=&%-]+")


def field(payload: dict[str, Any], key: str) -> str:
    """Reads KEY as a string; missing, null and false read as empty."""
    value = payload.get(key)
    if value is None or value is False:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def _check(pattern: re.Pattern[str], value: str, message: str) -> None:
    if not pattern.fullmatch(value):
        raise Rejected(message)


def validate(payload: object, hook_site: str, config: Config, now: int) -> str:
    """Checks a payload against the site table; returns the URL it deploys to ("" for a
    teardown) or raises Rejected with a reason."""
    if not isinstance(payload, dict):
        raise Rejected("payload is not a JSON object")

    protocol_version = field(payload, "protocol") or str(protocol.PROTOCOL)
    if protocol_version != str(protocol.PROTOCOL):
        raise Rejected(f"unsupported protocol '{protocol_version}' (this host speaks {protocol.PROTOCOL})")

    site = field(payload, "site")
    action = field(payload, "action")
    env = field(payload, "env")
    branch = field(payload, "branch")
    sha = field(payload, "sha")
    ts = field(payload, "ts")

    _check(_ID, field(payload, "id"), "invalid id")
    if site != hook_site:
        raise Rejected("site does not match hook")
    if site not in config.sites:
        raise Rejected("unknown site")
    _check(_TS, ts, "invalid ts")
    if abs(int(ts) - now) > config.max_skew:
        raise Rejected(f"stale or future request (ts={ts} now={now})")
    _check(_BRANCH, branch, "invalid branch")
    if ".." in branch:
        raise Rejected("invalid branch")

    if action == "deploy":
        if env not in protocol.ENVS:
            raise Rejected("invalid env")
        if env not in config.sites[site].envs:
            raise Rejected(f"env '{env}' not allowed for site '{site}'")
        _check(_SHA, sha, "invalid sha")
        _check(_SHA256, field(payload, "sha256"), "invalid sha256")
        artifact_url = field(payload, "artifact_url")
        prefix = config.sites[site].artifact_url_prefix
        if not artifact_url.startswith(prefix):
            raise Rejected(f"artifact_url not allowed (must start with {prefix})")
        _check(_ARTIFACT_URL, artifact_url, "invalid artifact_url")
        if ".." in artifact_url:
            raise Rejected("invalid artifact_url")
        workflow_url = field(payload, "workflow_url")
        if workflow_url:
            _check(_ARTIFACT_URL, workflow_url, "invalid workflow_url")
            if ".." in workflow_url:
                raise Rejected("invalid workflow_url")
        return config.slot_url(site, env, branch, sha)
    if action == "teardown":
        if env != "preview":
            raise Rejected("teardown only applies to previews")
        return ""
    raise Rejected("invalid action")


def spool(state: StateDir, id: str, raw: str) -> None:
    """Queues RAW for the worker. Creating the spool file is the replay gate: an id is accepted once."""
    if state.status_path(id).exists():
        raise Rejected("id already used")
    try:
        fd = os.open(state.spool_path(id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Rejected("id already used") from None
    with os.fdopen(fd, "w") as f:
        f.write(raw)


def detach_worker(config: Config, id: str) -> None:
    """Runs the worker in a forked session that outlives this process.

    The child must not keep webhook's stdout/stderr pipes open, or webhook
    would wait for the deploy to finish before answering.
    """
    if os.fork() != 0:
        return
    code = 1
    try:
        os.setsid()
        log = os.open(config.state.log_path(id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.dup2(log, 1)
        os.dup2(log, 2)
        sys.stdout = os.fdopen(1, "w", buffering=1)
        sys.stderr = os.fdopen(2, "w", buffering=1)
        from . import worker

        code = worker.run(config, id)
    finally:
        os._exit(code)


def handle(
    raw: str,
    hook_site: str,
    config: Config,
    now: int,
    spawn: Callable[[Config, str], None] = detach_worker,
) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise Rejected("payload is not a JSON object") from None
    url = validate(payload, hook_site, config, now)
    id = field(payload, "id")
    status_meta = {
        "action": field(payload, "action"),
        "site": field(payload, "site"),
        "env": field(payload, "env"),
        "branch": field(payload, "branch"),
        "sha": field(payload, "sha"),
        "repo": config.sites[field(payload, "site")].repo,
        "workflow_url": field(payload, "workflow_url"),
    }
    spool(config.state, id, raw)
    config.state.write_status(id, "queued", "queued", url, status_meta)
    index.write(config)
    spawn(config, id)
    return {"id": id, "state": "queued", "url": url, "status_url": config.status_url(id)}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: muelle-receive SITE", file=sys.stderr)
        return 2
    raw = os.environ.pop("MUELLE_PAYLOAD", "")
    try:
        if not raw:
            raise Rejected("no payload")
        response = handle(raw, args[0], Config.load(), int(time.time()))
    except Rejected as e:
        print(f"muelle: {e}", file=sys.stderr)
        return 1
    print(protocol.encode(response).decode())
    return 0
