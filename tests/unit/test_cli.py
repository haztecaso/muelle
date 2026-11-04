import io
import json
import os

import pytest
from test_index import deployed

from muelle import cli
from muelle.config import Config


def run(config: Config, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.run(list(argv), config, out)
    return code, out.getvalue()


def host_statuses(config: Config) -> list[dict[str, str]]:
    paths = (config.state_dir / "status").glob("host-*.json")
    return [json.loads(p.read_text()) for p in paths]


def test_status_lists_slots(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "feat/x", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "prod", "prod", "main", "2026-01-02T00:00:00Z")
    code, out = run(config, "status", "demo")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "demo  https://demo.test"
    assert lines[2].split()[:4] == ["prod", "main", "x", "1"]
    assert lines[3].split()[:2] == ["aaaaaaaa", "feat/x"]
    assert lines[3].endswith("https://aaaaaaaa-demo.muelle.test")


def test_status_all_sites(config: Config) -> None:
    _, out = run(config, "status")
    assert "demo  https://demo.test\n  nothing deployed" in out
    assert "prodonly  https://prodonly.test" in out


def test_unknown_site(config: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(config, "status", "ghost")[0] == 1
    assert "unknown site 'ghost'" in capsys.readouterr().err


def test_releases_marks_current(config: Config) -> None:
    deployed(config, "demo", "prod", "prod", "main", "2026-01-01T00:00:00Z")
    old = config.base_dir / "demo" / "prod" / "releases" / "old"
    old.mkdir()
    os.utime(old, (0, 0))
    _, out = run(config, "releases", "demo", "prod")
    assert [line.split()[:2] for line in out.splitlines()] == [["*", "x"], ["old", "1970-01-01T00:00:00Z"]]


def test_releases_rejects_bad_slot(config: Config) -> None:
    assert run(config, "releases", "demo", "../etc")[0] == 1


def test_teardown_is_recorded(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "feat/x", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "bbbbbbbb", "preview", "feat/y", "2026-01-01T00:00:00Z")
    code, out = run(config, "teardown", "demo", "feat/x")
    assert code == 0
    assert "removed 1 preview(s) for branch feat/x" in out
    assert not (config.base_dir / "demo" / "aaaaaaaa").exists()
    assert (config.base_dir / "demo" / "bbbbbbbb").exists()
    [status] = host_statuses(config)
    assert status["state"] == "success"
    assert status["message"] == "teardown of feat/x done"
    assert "removed 1 preview" in config.state.log_path(status["id"]).read_text()
    assert "feat/x" not in (config.state_dir / "index" / "index.json").read_text()


def test_rm_slot(config: Config) -> None:
    deployed(config, "demo", "next", "next", "main", "2026-01-01T00:00:00Z")
    assert run(config, "rm", "demo", "next")[0] == 0
    assert not (config.base_dir / "demo" / "next").exists()


@pytest.mark.parametrize("slot", ["prod", "..", "nope"])
def test_rm_refuses(config: Config, slot: str) -> None:
    deployed(config, "demo", "prod", "prod", "main", "2026-01-01T00:00:00Z")
    assert run(config, "rm", "demo", slot)[0] == 1
    assert (config.base_dir / "demo" / "prod").exists()
    assert host_statuses(config) == []


def test_prune_no_record(config: Config) -> None:
    assert run(config, "prune", "--no-record")[0] == 0
    assert host_statuses(config) == []


def test_prune_applies_keep_limits_but_keeps_current(config: Config) -> None:
    # keep_releases = 2 in the fixture; the current release is the oldest
    deployed(config, "demo", "prod", "prod", "main", "2026-01-01T00:00:00Z")
    releases = config.base_dir / "demo" / "prod" / "releases"
    os.utime(releases / "x", (0, 0))
    for i, name in enumerate(["a", "b", "c"], start=1):
        (releases / name).mkdir()
        os.utime(releases / name, (i * 100, i * 100))
    assert run(config, "prune", "demo")[0] == 0
    assert sorted(p.name for p in releases.iterdir()) == ["b", "c", "x"]
    [status] = host_statuses(config)
    assert status["message"] == "prune demo done"


def test_jobs_and_log(config: Config) -> None:
    config.state.write_status("old-job-0001", "success", "deployed a")
    deployed(config, "demo", "aaaaaaaa", "preview", "feat/x", "2026-01-01T00:00:00Z")
    run(config, "teardown", "demo", "feat/x")
    [status] = host_statuses(config)
    _, out = run(config, "jobs", "-n", "5")
    assert sorted(line.split()[2] for line in out.splitlines()) == sorted([status["id"], "old-job-0001"])
    _, out = run(config, "jobs", "-n", "1")
    assert len(out.splitlines()) == 1
    _, out = run(config, "jobs", "demo")
    [line] = out.splitlines()
    assert line.split()[1:7] == ["success", status["id"], "demo", "teardown", "preview", "feat/x"]
    _, out = run(config, "jobs", "prodonly")
    assert out == ""
    _, log = run(config, "log", status["id"])
    assert "teardown of feat/x (host)" in log
    assert run(config, "log", "../../etc/passwd")[0] == 1


def test_index_rebuilds_listing(config: Config) -> None:
    deployed(config, "demo", "next", "next", "main", "2026-01-01T00:00:00Z")
    assert run(config, "index")[0] == 0
    assert "next-demo" in (config.state_dir / "index" / "index.json").read_text()
