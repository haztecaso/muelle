"""muelle-prune (also `muelle prune`)

Removes preview slots whose last deploy is older than the site's preview TTL,
then applies keepPreviews and keepReleases (never the release `current`
points to), and refreshes the listing. Run daily by muelle-prune.timer.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import index, worker
from .config import Config
from .worker import log, preview_slots


def deployed_at(slot: Path) -> datetime:
    """Time of the slot's last deploy; the dir's mtime if meta.json is missing or bad."""
    try:
        return datetime.strptime(index.read_meta(slot)["deployed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except KeyError, TypeError, ValueError:
        return datetime.fromtimestamp(slot.stat().st_mtime, UTC)


def expired(config: Config, site: str, now: datetime) -> list[Path]:
    ttl = config.sites[site].preview_ttl_days
    if ttl is None:
        return []
    cutoff = now - timedelta(days=ttl)
    return [s for s in preview_slots(config.base_dir / site) if deployed_at(s) < cutoff]


def prune(config: Config, now: datetime | None = None, sites: list[str] | None = None) -> int:
    """Prunes SITES (default: all); returns how many expired previews were removed."""
    now = now or datetime.now(UTC)
    removed = 0
    for site in sites or config.sites:
        site_dir = config.base_dir / site
        # same lock as the worker: never races a deploy to this site
        with config.state.lock(site):
            for slot in expired(config, site, now):
                log(f"removing expired preview {site}/{slot.name}")
                shutil.rmtree(slot)
                removed += 1
            # the worker applies these on deploy; here they catch lowered limits
            worker.prune(preview_slots(site_dir), config.keep_previews, lambda s: worker.mtime(s / "meta.json"))
            for slot in worker.subdirs(site_dir):
                releases = worker.subdirs(slot / "releases")
                worker.prune(releases, config.keep_releases, worker.mtime, protect=worker.current_release(slot))
    index.write(config)
    log(f"removed {removed} expired preview(s)")
    return removed


def main() -> int:
    prune(Config.load())
    return 0
