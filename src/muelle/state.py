"""Files under stateDir: status, jobs, spool, logs and locks."""

import fcntl
import json
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

type State = Literal["queued", "in_progress", "success", "failure"]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(dest: Path, content: str, mode: int = 0o644) -> None:
    """Replaces DEST so readers (nginx) never see a partial file."""
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=".tmp.")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        tmp.chmod(mode)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json_atomic(dest: Path, data: Any) -> None:
    write_atomic(dest, json.dumps(data, indent=2) + "\n")


class StateDir:
    def __init__(self, root: Path) -> None:
        self.root = root

    def status_path(self, id: str) -> Path:
        return self.root / "status" / f"{id}.json"

    def spool_path(self, id: str) -> Path:
        return self.root / "spool" / f"{id}.json"

    def log_path(self, id: str) -> Path:
        return self.root / "log" / f"{id}.log"

    def job_path(self, id: str) -> Path:
        return self.root / "jobs" / f"{id}.json"

    def write_job(self, id: str, **fields: str) -> None:
        """Records what job ID is about (site, action, env, branch, ...). Kept apart from
        the status file, which /status/ serves publicly: branch names stay off it."""
        write_json_atomic(self.job_path(id), {"id": id, **fields, "created": utc_now()})

    def read_job(self, id: str) -> dict[str, Any]:
        try:
            return json.loads(self.job_path(id).read_text())
        except OSError, ValueError:
            return {}

    def write_status(self, id: str, state: State, message: str, url: str = "") -> None:
        write_json_atomic(
            self.status_path(id),
            {"id": id, "state": state, "message": message, "url": url, "updated": utc_now()},
        )

    @contextmanager
    def lock(self, name: str) -> Generator[None]:
        """Holds an exclusive flock on locks/NAME.lock, blocking until it is free."""
        with (self.root / "locks" / f"{name}.lock").open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
