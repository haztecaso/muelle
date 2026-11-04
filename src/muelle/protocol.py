"""Wire format shared by the client (CI runner) and the receiver (host).

The client imports this module too, so it must stay stdlib-only.
"""

import hashlib
import hmac
import json
import re
from typing import Any, Literal

# Bumped together with the action's major version on breaking payload changes.
# A payload without the field comes from before it existed (protocol 1).
PROTOCOL = 1

SIGNATURE_HEADER = "X-Muelle-Signature"

type Env = Literal["prod", "next", "preview"]
type Action = Literal["deploy", "teardown"]
ENVS: tuple[Env, ...] = ("prod", "next", "preview")


def encode(payload: dict[str, Any]) -> bytes:
    """Serializes a payload exactly as it is signed and sent."""
    return json.dumps(payload, separators=(",", ":")).encode()


def sign(body: bytes, secret: str) -> str:
    """Returns the X-Muelle-Signature header value for BODY."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def slot_for(site: str, env: str, branch: str, sha: str) -> str:
    """Directory name of an environment: prod, next, or 8 hex chars per preview commit."""
    if env in ("prod", "next"):
        return env
    return hashlib.sha256(f"{site}\n{branch}\n{sha}".encode()).hexdigest()[:8]


def is_preview_slot(name: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{8}", name) is not None


def deploy_payload(
    *,
    id: str,
    site: str,
    env: str,
    branch: str,
    sha: str,
    sha256: str,
    artifact_url: str,
    token: str,
    ts: int,
    workflow_url: str = "",
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "id": id,
        "site": site,
        "action": "deploy",
        "env": env,
        "branch": branch,
        "sha": sha,
        "sha256": sha256,
        "artifact_url": artifact_url,
        "token": token,
        "workflow_url": workflow_url,
        "ts": ts,
    }


def teardown_payload(*, id: str, site: str, branch: str, ts: int) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "id": id,
        "site": site,
        "action": "teardown",
        "env": "preview",
        "branch": branch,
        "ts": ts,
    }
