import json
import os
from pathlib import Path
from tarfile import SYMTYPE, TarInfo
from typing import Any

import pytest
from helpers import make_tar, make_zip, sha256

from muelle import protocol, worker
from muelle.config import Config
from muelle.download import DownloadError

SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40


class FakeArtifacts:
    """Stands in for the GitHub artifact API."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.tokens: list[str] = []

    def add(self, name: str, data: bytes) -> tuple[str, str]:
        url = f"https://api.github.com/repos/owner/demo/{name}"
        self.files[url] = data
        return url, sha256(data)

    def __call__(self, url: str, dest: Path, *, token: str, max_bytes: int) -> None:
        self.tokens.append(token)
        if url not in self.files:
            raise DownloadError("download failed: HTTP 404")
        dest.write_bytes(self.files[url])


@pytest.fixture
def artifacts() -> FakeArtifacts:
    return FakeArtifacts()


counter = iter(range(1000))


def spool(config: Config, payload: dict[str, Any]) -> str:
    id = f"test-{next(counter):04d}"
    (config.state_dir / "spool" / f"{id}.json").write_text(json.dumps(payload))
    return id


def status(config: Config, id: str) -> dict[str, Any]:
    return json.loads((config.state_dir / "status" / f"{id}.json").read_text())


def run_deploy(
    config: Config, artifacts: FakeArtifacts, env: str, sha: str, data: bytes, branch: str = "main"
) -> tuple[str, dict[str, Any]]:
    url, digest = artifacts.add(f"{next(counter)}.zip", data)
    payload = protocol.deploy_payload(
        id="",
        site="demo",
        env=env,
        branch=branch,
        sha=sha,
        sha256=digest,
        artifact_url=url,
        token="ghs_dummy",
        ts=0,
    )
    id = spool(config, payload)
    code = worker.run(config, id, fetch=artifacts)
    result = status(config, id)
    assert (code == 0) == (result["state"] == "success"), result
    return id, result


def test_prune_keeps_newest_and_protected(tmp_path: Path) -> None:
    dirs = []
    for name, mtime in (("a", 1), ("c", 3), ("b", 2), ("z", 0.5)):
        (tmp_path / name).mkdir()
        os.utime(tmp_path / name, (mtime, mtime))
        dirs.append(tmp_path / name)
    worker.prune(dirs, 10, worker._mtime)
    assert len(list(tmp_path.iterdir())) == 4
    worker.prune(dirs, 2, worker._mtime, protect="a")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b", "c"]


def test_prod_deploy(config: Config, artifacts: FakeArtifacts) -> None:
    id, result = run_deploy(config, artifacts, "prod", SHA1, make_zip({"index.html": "prod-1"}))
    assert result["state"] == "success"
    assert result["message"] == f"deployed {SHA1}"
    assert result["url"] == "https://demo.test"
    slot = config.base_dir / "demo" / "prod"
    assert (slot / "current").readlink() == Path("releases") / SHA1
    assert (slot / "current" / "index.html").read_text() == "prod-1"
    meta = json.loads((slot / "meta.json").read_text())
    assert meta["sha"] == SHA1
    assert meta["env"] == "prod"
    assert artifacts.tokens == ["ghs_dummy"]
    assert not (config.state_dir / "spool" / f"{id}.json").exists()
    assert (config.state_dir / "index" / "index.html").exists()


def test_redeploy_flips_current_and_prunes_releases(config: Config, artifacts: FakeArtifacts) -> None:
    releases = config.base_dir / "demo" / "next" / "releases"
    for i, sha in enumerate((SHA1, SHA2, SHA3)):
        run_deploy(config, artifacts, "next", sha, make_tar([("dist/index.html", f"v{i}")]))
        # mtime resolution: make the order explicit
        os.utime(releases / sha, (1000 + i, 1000 + i))
    assert (config.base_dir / "demo" / "next" / "current" / "index.html").read_text() == "v2"
    # keepReleases = 2: the oldest is gone
    assert sorted(p.name for p in releases.iterdir()) == [SHA2, SHA3]


def test_redeploy_of_same_sha_replaces_release(config: Config, artifacts: FakeArtifacts) -> None:
    run_deploy(config, artifacts, "next", SHA1, make_zip({"index.html": "old"}))
    run_deploy(config, artifacts, "next", SHA1, make_zip({"index.html": "new"}))
    assert (config.base_dir / "demo" / "next" / "current" / "index.html").read_text() == "new"


def test_previews_are_pruned(config: Config, artifacts: FakeArtifacts) -> None:
    site = config.base_dir / "demo"
    slots = []
    for i, sha in enumerate((SHA1, SHA2, SHA3)):
        _, result = run_deploy(config, artifacts, "preview", sha, make_zip({"index.html": "p"}), branch="feat/x")
        slot = result["url"].removeprefix("https://").split("-")[0]
        slots.append(slot)
        os.utime(site / slot / "meta.json", (1000 + i, 1000 + i))
    run_deploy(config, artifacts, "next", SHA1, make_zip({"index.html": "n"}))
    remaining = sorted(p.name for p in site.iterdir() if protocol.is_preview_slot(p.name))
    # keepPreviews = 2; the prune after the third preview removed the first
    assert remaining == sorted(slots[1:])


def test_teardown_removes_branch_previews(config: Config, artifacts: FakeArtifacts) -> None:
    run_deploy(config, artifacts, "preview", SHA1, make_zip({"index.html": "x"}), branch="feat/x")
    _, kept = run_deploy(config, artifacts, "preview", SHA2, make_zip({"index.html": "y"}), branch="feat/y")
    id = spool(config, protocol.teardown_payload(id="", site="demo", branch="feat/x", ts=0))
    assert worker.run(config, id, fetch=artifacts) == 0
    assert status(config, id)["message"] == "teardown of feat/x done"
    remaining = [p.name for p in (config.base_dir / "demo").iterdir() if protocol.is_preview_slot(p.name)]
    assert remaining == [protocol.slot_for("demo", "preview", "feat/y", SHA2)]
    index = json.loads((config.state_dir / "index" / "index.json").read_text())
    assert [e["branch"] for e in index["entries"]] == ["feat/y"]
    assert kept["url"] == index["entries"][0]["url"]


def test_sha256_mismatch_fails(config: Config, artifacts: FakeArtifacts) -> None:
    url, _ = artifacts.add("junk.zip", b"junk")
    payload = protocol.deploy_payload(
        id="",
        site="demo",
        env="next",
        branch="main",
        sha=SHA1,
        sha256="0" * 64,
        artifact_url=url,
        token="",
        ts=0,
    )
    id = spool(config, payload)
    assert worker.run(config, id, fetch=artifacts) == 1
    result = status(config, id)
    assert result["state"] == "failure"
    assert "sha256 mismatch" in result["message"]
    assert result["url"] == "https://next-demo.muelle.test"
    assert not (config.base_dir / "demo" / "next" / "current").exists()


def test_failed_deploy_keeps_previous_release(config: Config, artifacts: FakeArtifacts) -> None:
    run_deploy(config, artifacts, "next", SHA1, make_zip({"index.html": "good"}))
    evil = TarInfo("index.html")
    evil.type = SYMTYPE
    evil.linkname = "/etc/passwd"
    _, result = run_deploy(config, artifacts, "next", SHA2, make_tar([evil]))
    assert result["state"] == "failure"
    assert "non-regular" in result["message"]
    assert (config.base_dir / "demo" / "next" / "current" / "index.html").read_text() == "good"
    assert not list((config.base_dir / "demo" / "next").glob(".tmp.*"))


def test_download_failure_is_reported(config: Config, artifacts: FakeArtifacts) -> None:
    payload = protocol.deploy_payload(
        id="",
        site="demo",
        env="next",
        branch="main",
        sha=SHA1,
        sha256="0" * 64,
        artifact_url="https://api.github.com/repos/owner/demo/missing",
        token="",
        ts=0,
    )
    id = spool(config, payload)
    assert worker.run(config, id, fetch=artifacts) == 1
    assert status(config, id)["message"] == "failed: download failed: HTTP 404"


def test_missing_spool_is_reported(config: Config) -> None:
    assert worker.run(config, "test-missing") == 1
    assert status(config, "test-missing")["state"] == "failure"


def test_unexpected_error_is_not_exposed(
    config: Config, artifacts: FakeArtifacts, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object, **_kw: object) -> None:
        raise OSError(f"cannot write {config.base_dir}/secret")

    payload = protocol.deploy_payload(
        id="",
        site="demo",
        env="next",
        branch="main",
        sha=SHA1,
        sha256="0" * 64,
        artifact_url="https://api.github.com/repos/owner/demo/x",
        token="",
        ts=0,
    )
    id = spool(config, payload)
    assert worker.run(config, id, fetch=boom) == 1
    result = status(config, id)
    assert result["message"] == f"failed: internal error (see host log {id})"
    assert result["url"] == "https://next-demo.muelle.test"
    out = capsys.readouterr().out
    assert f"failed: OSError: cannot write {config.base_dir}/secret" in out
    assert "Traceback" in out
