"""muelle: admin CLI on the host.

Operations that should not depend on CI being available. Reads the same
MUELLE_CONFIG and takes the same locks as the worker; the NixOS module's
`muelle` wrapper sets MUELLE_CONFIG and runs it as the muelle user.

Commands that change slots (prune, teardown, rm) are recorded like hook
requests: a `host-...` status file and log, then the listing is refreshed.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from . import index, protocol, worker
from . import prune as prune_mod
from .config import Config
from .state import utc_now


class CliError(Exception):
    pass


def _site(config: Config, name: str) -> str:
    if name not in config.sites:
        raise CliError(f"unknown site '{name}' (known: {', '.join(config.sites)})")
    return name


def _sites(config: Config, name: str | None) -> list[str]:
    return [_site(config, name)] if name else list(config.sites)


def _slot(slot: str) -> str:
    if slot not in ("prod", "next") and not protocol.is_preview_slot(slot):
        raise CliError(f"invalid slot '{slot}' (prod, next or a preview hash)")
    return slot


def _when(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table(rows: list[list[str]], out: IO[str]) -> None:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))] if rows else []
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip(), file=out)


def _slots(config: Config, site: str) -> list[Path]:
    """prod, next, then previews newest first."""
    order = {"prod": 0, "next": 1}
    slots = [p for p in worker.subdirs(config.base_dir / site) if p.name in order or protocol.is_preview_slot(p.name)]
    slots.sort(key=lambda p: worker.mtime(p / "meta.json"), reverse=True)
    slots.sort(key=lambda p: order.get(p.name, 2))
    return slots


def status(config: Config, sites: list[str], out: IO[str]) -> None:
    for site in sites:
        print(f"{site}  {config.sites[site].prod_url}", file=out)
        rows = [["  slot", "branch", "current", "releases", "deployed", "url"]]
        for slot in _slots(config, site):
            meta = index.read_meta(slot)
            current = worker.current_release(slot)
            rows.append(
                [
                    f"  {slot.name}",
                    str(meta.get("branch", "-")),
                    current[:12] or "-",
                    str(len(worker.subdirs(slot / "releases"))),
                    str(meta.get("deployed_at", "-")),
                    config.url_for(site, slot.name),
                ]
            )
        if len(rows) == 1:
            print("  nothing deployed", file=out)
        else:
            _table(rows, out)


def releases(config: Config, site: str, slot: str, out: IO[str]) -> None:
    slot_dir = config.base_dir / site / _slot(slot)
    if not slot_dir.is_dir():
        raise CliError(f"no slot '{slot}' for site '{site}'")
    current = worker.current_release(slot_dir)
    rels = sorted(worker.subdirs(slot_dir / "releases"), key=worker.mtime, reverse=True)
    _table([["*" if r.name == current else " ", r.name, _when(r)] for r in rels], out)


JOB_COLUMNS = ("updated", "state", "id", "site", "action", "env", "branch", "message")


def jobs(config: Config, limit: int, site: str | None, out: IO[str]) -> None:
    entries: list[dict[str, Any]] = []
    for path in (config.state_dir / "status").glob("*.json"):
        try:
            status = json.loads(path.read_text())
        except OSError, ValueError:
            continue
        entry = config.state.read_job(path.stem) | status
        if site is None or entry.get("site") == site:
            entries.append(entry)
    entries.sort(key=lambda e: str(e.get("updated", "")), reverse=True)
    _table([[str(e.get(k) or "-") for k in JOB_COLUMNS] for e in entries[:limit]], out)


_ID = re.compile(r"[A-Za-z0-9-]+")


def show_log(config: Config, id: str, out: IO[str]) -> None:
    if not _ID.fullmatch(id):
        raise CliError("invalid id")
    path = config.state.log_path(id)
    if not path.exists():
        raise CliError(f"no log for '{id}'")
    out.write(path.read_text())


class _Tee:
    def __init__(self, *streams: IO[str]) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for s in self.streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


@contextmanager
def host_job(config: Config, description: str, out: IO[str], **job: str) -> Generator[str]:
    """Records a host action like a hook request: job and status files, log, then the listing."""
    id = f"host-{int(time.time())}-{secrets.token_hex(4)}"
    state = config.state
    state.write_job(id, **job)
    state.write_status(id, "in_progress", description)
    # same mode as the worker's logs
    fd = os.open(state.log_path(id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    with os.fdopen(fd, "a") as log, redirect_stdout(_Tee(out, log)):
        worker.log(f"[{utc_now()}] {description} (host)")
        try:
            yield id
            index.write(config)
        except BaseException as e:
            worker.log(f"failed: {e!r}")
            state.write_status(id, "failure", f"failed: {description}")
            raise
    state.write_status(id, "success", f"{description} done")


def teardown(config: Config, site: str, branch: str, out: IO[str]) -> None:
    job = {"site": site, "action": "teardown", "env": "preview", "branch": branch}
    with host_job(config, f"teardown of {branch}", out, **job), config.state.lock(site):
        worker.teardown(config, site, branch)


def rm(config: Config, site: str, slot: str, out: IO[str]) -> None:
    if _slot(slot) == "prod":
        raise CliError("refusing to remove prod")
    slot_dir = config.base_dir / site / slot
    if not slot_dir.is_dir():
        raise CliError(f"no slot '{slot}' for site '{site}'")
    job = {"site": site, "action": "rm", "env": "next" if slot == "next" else "preview", "slot": slot}
    with host_job(config, f"rm {site}/{slot}", out, **job), config.state.lock(site):
        worker.log(f"removing {slot_dir}")
        shutil.rmtree(slot_dir)


def prune(config: Config, sites: list[str], out: IO[str]) -> None:
    with host_job(config, f"prune {' '.join(sites)}", out, site=",".join(sites), action="prune"):
        prune_mod.prune(config, sites=sites)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="muelle", description="muelle admin CLI")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status", help="slots, current release and URL of each site")
    s.add_argument("site", nargs="?")
    s = sub.add_parser("releases", help="kept releases of a slot (* = current)")
    s.add_argument("site")
    s.add_argument("slot", help="prod, next or a preview hash")
    s = sub.add_parser("prune", help="apply previewTtlDays, keepPreviews and keepReleases now")
    s.add_argument("site", nargs="?")
    s.add_argument("--no-record", action="store_true", help="no host job (used by muelle-prune.timer)")
    s = sub.add_parser("teardown", help="remove every preview of a branch")
    s.add_argument("site")
    s.add_argument("branch")
    s = sub.add_parser("rm", help="remove one next or preview slot")
    s.add_argument("site")
    s.add_argument("slot")
    sub.add_parser("index", help="rebuild the listing")
    s = sub.add_parser("jobs", help="recent jobs, newest first")
    s.add_argument("site", nargs="?")
    s.add_argument("-n", type=int, default=20, help="how many (default 20)")
    s = sub.add_parser("log", help="log of a job")
    s.add_argument("id")
    return p


def run(argv: Sequence[str], config: Config, out: IO[str] = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    try:
        match args.command:
            case "status":
                status(config, _sites(config, args.site), out)
            case "releases":
                releases(config, _site(config, args.site), args.slot, out)
            case "prune" if args.no_record:
                prune_mod.prune(config, sites=_sites(config, args.site))
            case "prune":
                prune(config, _sites(config, args.site), out)
            case "teardown":
                teardown(config, _site(config, args.site), args.branch, out)
            case "rm":
                rm(config, _site(config, args.site), args.slot, out)
            case "index":
                index.write(config)
            case "jobs":
                jobs(config, args.n, args.site and _site(config, args.site), out)
            case "log":
                show_log(config, args.id, out)
    except CliError as e:
        print(f"muelle: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return run(sys.argv[1:], Config.load())
