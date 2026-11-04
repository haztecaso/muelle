import dataclasses
import os
from datetime import UTC, datetime

import pytest
from test_index import deployed

from muelle import prune
from muelle.config import Config

NOW = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def ttl_config(config: Config) -> Config:
    sites = {name: dataclasses.replace(s, preview_ttl_days=30) for name, s in config.sites.items()}
    return dataclasses.replace(config, sites=sites)


def test_expired_previews_are_removed(ttl_config: Config) -> None:
    deployed(ttl_config, "demo", "aaaaaaaa", "preview", "old", "2026-01-01T00:00:00Z")
    deployed(ttl_config, "demo", "bbbbbbbb", "preview", "fresh", "2026-02-15T00:00:00Z")
    deployed(ttl_config, "demo", "next", "next", "main", "2025-01-01T00:00:00Z")
    assert prune.prune(ttl_config, NOW) == 1
    site = ttl_config.base_dir / "demo"
    assert not (site / "aaaaaaaa").exists()
    assert (site / "bbbbbbbb").exists()
    assert (site / "next").exists()
    assert "fresh" in (ttl_config.state_dir / "index" / "index.json").read_text()


def test_ttl_boundary_is_kept(ttl_config: Config) -> None:
    deployed(ttl_config, "demo", "aaaaaaaa", "preview", "edge", "2026-01-30T00:00:00Z")
    assert prune.prune(ttl_config, NOW) == 0


def test_no_ttl_keeps_everything(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "old", "2020-01-01T00:00:00Z")
    assert prune.prune(config, NOW) == 0
    assert (config.base_dir / "demo" / "aaaaaaaa").exists()


def test_slot_without_meta_uses_mtime(ttl_config: Config) -> None:
    old = ttl_config.base_dir / "demo" / "cccccccc"
    (old / "releases").mkdir(parents=True)
    os.utime(old, (0, 0))
    fresh = ttl_config.base_dir / "demo" / "dddddddd"
    fresh.mkdir()
    (fresh / "meta.json").write_text("not json")
    os.utime(fresh, (NOW.timestamp(), NOW.timestamp()))
    assert prune.prune(ttl_config, NOW) == 1
    assert not old.exists()
    assert fresh.exists()


def test_missing_site_dir_is_fine(ttl_config: Config) -> None:
    assert prune.prune(ttl_config, NOW) == 0
