"""muelle-worker ID

Detached by muelle-receive. Reads the already-validated payload from the
spool dir, deploys or tears down, and keeps the status file updated.
"""

import json
import shutil
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import index, protocol
from .archive import ArchiveError, extract
from .config import Config
from .download import DownloadError, download, sha256_file
from .state import StateDir, utc_now, write_json_atomic

PREVIEW_SLOT_LEN = 8

type Downloader = Callable[..., None]


class DeployError(Exception):
    pass


class Terminated(BaseException):
    """Raised on SIGTERM (e.g. service restart) so the failure gets recorded."""


@dataclass(frozen=True)
class Job:
    id: str
    site: str
    action: str
    env: str
    branch: str
    sha: str
    sha256: str
    artifact_url: str
    token: str

    @classmethod
    def from_payload(cls, id: str, payload: dict[str, Any]) -> Job:
        def get(key: str) -> str:
            return str(payload.get(key) or "")

        return cls(
            id=id,
            site=get("site"),
            action=get("action"),
            env=get("env"),
            branch=get("branch"),
            sha=get("sha"),
            sha256=get("sha256"),
            artifact_url=get("artifact_url"),
            token=get("token"),
        )


def log(message: str) -> None:
    print(message, flush=True)


def is_preview_slot(name: str) -> bool:
    return len(name) == PREVIEW_SLOT_LEN and all(c in "0123456789abcdef" for c in name)


def _subdirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [p for p in directory.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")]


def surplus(entries: list[tuple[float, str]], keep: int, protect: str = "") -> list[str]:
    """Names beyond the newest KEEP (by timestamp), never including PROTECT."""
    ranked = sorted(entries, key=lambda e: e[0], reverse=True)
    return [name for _, name in ranked[keep:] if name != protect]


def prune_releases(releases: Path, keep: int, protect: str) -> None:
    entries = [(p.stat().st_mtime, p.name) for p in _subdirs(releases)]
    for name in surplus(entries, keep, protect):
        log(f"pruning {releases}/{name}")
        shutil.rmtree(releases / name)


def preview_slots(site_dir: Path) -> list[Path]:
    return [p for p in _subdirs(site_dir) if is_preview_slot(p.name)]


def prune_previews(site_dir: Path, keep: int) -> None:
    def deployed(slot: Path) -> float:
        meta = slot / "meta.json"
        return meta.stat().st_mtime if meta.exists() else 0

    entries = [(deployed(p), p.name) for p in preview_slots(site_dir)]
    for name in surplus(entries, keep):
        log(f"pruning preview {name}")
        shutil.rmtree(site_dir / name)


def read_meta(slot_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((slot_dir / "meta.json").read_text())
    except OSError, ValueError:
        return {}


def deploy(config: Config, job: Job, state: StateDir, fetch: Downloader = download) -> str:
    """Installs the artifact as the slot's current release; returns the slot URL."""
    site_dir = config.base_dir / job.site
    slot = protocol.slot_for(job.site, job.env, job.branch, job.sha)
    url = config.url_for(job.site, slot)
    slot_dir = site_dir / slot
    releases = slot_dir / "releases"
    release = releases / job.sha

    state.write_status(job.id, "in_progress", "downloading", url)
    releases.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=slot_dir, prefix=".tmp.") as tmp_name:
        tmp = Path(tmp_name)
        artifact = tmp / "artifact"
        fetch(job.artifact_url, artifact, token=job.token, max_bytes=config.max_artifact_bytes)
        if sha256_file(artifact) != job.sha256:
            raise DeployError("sha256 mismatch")

        state.write_status(job.id, "in_progress", "extracting", url)
        root = tmp / "root"
        extract(artifact, root)

        if release.exists():
            shutil.rmtree(release)
        root.rename(release)
    release.touch()

    current_tmp = slot_dir / ".current.tmp"
    current_tmp.unlink(missing_ok=True)
    current_tmp.symlink_to(Path("releases") / job.sha)
    current_tmp.replace(slot_dir / "current")

    write_json_atomic(
        slot_dir / "meta.json",
        {"site": job.site, "env": job.env, "branch": job.branch, "sha": job.sha, "deployed_at": utc_now()},
    )

    prune_releases(releases, config.keep_releases, job.sha)
    if job.env == "preview":
        prune_previews(site_dir, config.keep_previews)
    log(f"deployed {url}")
    return url


def teardown(config: Config, job: Job) -> int:
    """Removes every preview of the job's branch; returns how many."""
    removed = 0
    for slot in preview_slots(config.base_dir / job.site):
        if read_meta(slot).get("branch") == job.branch:
            log(f"removing preview {slot.name}")
            shutil.rmtree(slot)
            removed += 1
    log(f"removed {removed} preview(s) for branch {job.branch}")
    return removed


def process(config: Config, job: Job, fetch: Downloader = download) -> None:
    state = StateDir(config.state_dir)
    (config.base_dir / job.site).mkdir(parents=True, exist_ok=True)
    with state.lock(job.site):
        log(f"[{utc_now()}] {job.action} site={job.site} env={job.env} branch={job.branch} sha={job.sha}")
        match job.action:
            case "deploy":
                url = deploy(config, job, state, fetch)
                index.write(config)
                state.write_status(job.id, "success", f"deployed {job.sha}", url)
            case "teardown":
                teardown(config, job)
                index.write(config)
                state.write_status(job.id, "success", f"teardown of {job.branch} done")
            case action:
                raise DeployError(f"invalid action '{action}'")


def _raise_terminated(signum: int, _frame: object) -> None:
    raise Terminated(f"killed by signal {signum}")


def run(config: Config, id: str, fetch: Downloader = download) -> int:
    """Entry point of the detached worker process: a kill is recorded as a failure."""
    signal.signal(signal.SIGTERM, _raise_terminated)
    signal.signal(signal.SIGINT, _raise_terminated)
    return process_spooled(config, id, fetch)


def process_spooled(config: Config, id: str, fetch: Downloader = download) -> int:
    """Processes spooled job ID, recording any failure in its status file."""
    state = StateDir(config.state_dir)
    url = ""
    try:
        spool = state.spool_path(id)
        payload = json.loads(spool.read_text())
        spool.unlink()
        job = Job.from_payload(id, payload)
        if job.action == "deploy":
            url = config.url_for(job.site, protocol.slot_for(job.site, job.env, job.branch, job.sha))
        process(config, job, fetch)
        return 0
    except (DeployError, DownloadError, ArchiveError, Terminated) as e:
        message = str(e)
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
    log(f"failed: {message}")
    state.write_status(id, "failure", f"failed: {message}", url)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: muelle-worker ID", file=sys.stderr)
        return 2
    return run(Config.load(), args[0])


if __name__ == "__main__":
    sys.exit(main())
