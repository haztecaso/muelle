"""muelle-worker ID

Detached by muelle-receive. Reads the already-validated payload from the
spool dir, deploys or tears down, and keeps the status file updated.
"""

import json
import shutil
import signal
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from . import index, protocol
from .archive import ArchiveError, extract
from .config import Config
from .download import DownloadError, download, sha256_file
from .state import StateDir, utc_now, write_json_atomic

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
        return cls(**{f.name: str(payload.get(f.name) or "") for f in fields(cls)} | {"id": id})


def log(message: str) -> None:
    print(message, flush=True)


def subdirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [p for p in directory.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")]


def prune(dirs: list[Path], keep: int, key: Callable[[Path], float], protect: str = "") -> None:
    """Removes DIRS beyond the newest KEEP (ranked by KEY), never PROTECT."""
    for path in sorted(dirs, key=key, reverse=True)[keep:]:
        if path.name != protect:
            log(f"pruning {path}")
            shutil.rmtree(path)


def mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0


def preview_slots(site_dir: Path) -> list[Path]:
    return [p for p in subdirs(site_dir) if protocol.is_preview_slot(p.name)]


def deploy(config: Config, job: Job, state: StateDir, fetch: Downloader = download) -> str:
    """Installs the artifact as the slot's current release; returns the slot URL."""
    site_dir = config.base_dir / job.site
    slot_dir = site_dir / protocol.slot_for(job.site, job.env, job.branch, job.sha)
    url = config.slot_url(job.site, job.env, job.branch, job.sha)
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
        extract(
            artifact,
            root,
            max_bytes=config.max_extracted_bytes,
            max_files=config.max_artifact_files,
        )

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

    prune(subdirs(releases), config.keep_releases, mtime, protect=job.sha)
    if job.env == "preview":
        prune(preview_slots(site_dir), config.keep_previews, lambda slot: mtime(slot / "meta.json"))
    log(f"deployed {url}")
    return url


def current_release(slot_dir: Path) -> str:
    """Name of the release `current` points to, "" if none."""
    try:
        return (slot_dir / "current").readlink().name
    except OSError:
        return ""


def teardown(config: Config, site: str, branch: str) -> int:
    """Removes every preview of BRANCH; returns how many."""
    removed = 0
    for slot in preview_slots(config.base_dir / site):
        if index.read_meta(slot).get("branch") == branch:
            log(f"removing preview {slot.name}")
            shutil.rmtree(slot)
            removed += 1
    log(f"removed {removed} preview(s) for branch {branch}")
    return removed


def process(config: Config, job: Job, fetch: Downloader = download) -> None:
    state = config.state
    (config.base_dir / job.site).mkdir(parents=True, exist_ok=True)
    with state.lock(job.site):
        log(f"[{utc_now()}] {job.action} site={job.site} env={job.env} branch={job.branch} sha={job.sha}")
        match job.action:
            case "deploy":
                url = deploy(config, job, state, fetch)
                message = f"deployed {job.sha}"
            case "teardown":
                teardown(config, job.site, job.branch)
                url, message = "", f"teardown of {job.branch} done"
            case action:
                raise DeployError(f"invalid action '{action}'")
        index.write(config)
        state.write_status(job.id, "success", message, url)


def _raise_terminated(signum: int, _frame: object) -> None:
    raise Terminated(f"killed by signal {signum}")


def run(config: Config, id: str, fetch: Downloader = download) -> int:
    """Entry point of the detached worker process: a kill is recorded as a failure."""
    signal.signal(signal.SIGTERM, _raise_terminated)
    signal.signal(signal.SIGINT, _raise_terminated)
    return process_spooled(config, id, fetch)


def process_spooled(config: Config, id: str, fetch: Downloader = download) -> int:
    """Processes spooled job ID, recording any failure in its status file."""
    state = config.state
    url = ""
    try:
        spool = state.spool_path(id)
        payload = json.loads(spool.read_text())
        spool.unlink()
        job = Job.from_payload(id, payload)
        if job.action == "deploy":
            url = config.slot_url(job.site, job.env, job.branch, job.sha)
        process(config, job, fetch)
        return 0
    except (DeployError, DownloadError, ArchiveError, Terminated) as e:
        # written for CI: safe to show on the public status endpoint
        message = str(e)
        log(f"failed: {message}")
    except Exception as e:
        # may hold host paths or other internals: full detail in the log only
        log(f"failed: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        message = f"internal error (see host log {id})"
    state.write_status(id, "failure", f"failed: {message}", url)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: muelle-worker ID", file=sys.stderr)
        return 2
    return run(Config.load(), args[0])
