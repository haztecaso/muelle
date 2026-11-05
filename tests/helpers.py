import hashlib
import io
import tarfile
import zipfile
from typing import Any

from muelle import protocol


def make_zip(files: dict[str, str], top: str = "dist") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(f"{top}/{name}" if top else name, content)
    return buf.getvalue()


def make_tar(entries: list[tarfile.TarInfo | tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                t.addfile(entry)
            else:
                name, content = entry
                data = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


NOW = 1_800_000_000
SHA = "1" * 40


def deploy(**overrides: Any) -> dict[str, Any]:
    payload = protocol.deploy_payload(
        id="test-0001",
        site="demo",
        env="next",
        branch="main",
        sha=SHA,
        sha256="0" * 64,
        artifact_url="https://api.github.com/repos/owner/demo/actions/artifacts/1/zip",
        token="ghs_dummy",
        ts=NOW,
    )
    payload.update(overrides)
    return payload
