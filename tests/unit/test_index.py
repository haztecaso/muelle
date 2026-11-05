import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from muelle import index
from muelle.config import Config


def deployed(config: Config, site: str, slot: str, env: str, branch: str, at: str, current: bool = True) -> None:
    slot_dir = config.base_dir / site / slot
    (slot_dir / "releases" / "x").mkdir(parents=True)
    if current:
        (slot_dir / "current").symlink_to("releases/x")
    meta = {"site": site, "env": env, "branch": branch, "sha": "a" * 40, "deployed_at": at}
    (slot_dir / "meta.json").write_text(json.dumps(meta))


def test_collect_orders_next_first_then_newest(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "old", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "bbbbbbbb", "preview", "new", "2026-02-01T00:00:00Z")
    deployed(config, "demo", "next", "next", "main", "2025-01-01T00:00:00Z")
    entries = index.collect(config)
    assert [e["branch"] for e in entries] == ["main", "new", "old"]
    assert entries[0]["url"] == "https://next-demo.muelle.test"
    assert entries[1]["url"] == "https://bbbbbbbb-demo.muelle.test"
    assert entries[0]["repo"] == "owner/demo"
    assert entries[0]["prod"] == "https://demo.test"


def test_collect_skips_prod_and_incomplete_slots(config: Config) -> None:
    deployed(config, "demo", "prod", "prod", "main", "2026-01-01T00:00:00Z")
    deployed(config, "demo", "cccccccc", "preview", "nocurrent", "2026-01-01T00:00:00Z", current=False)
    deployed(config, "demo", "notaslot", "preview", "weird", "2026-01-01T00:00:00Z")
    (config.base_dir / "demo" / ".tmp.abc").mkdir()
    assert index.collect(config) == []


def test_render_escapes_and_links(config: Config) -> None:
    deployed(config, "demo", "aaaaaaaa", "preview", "feat/<x>", "2026-01-01T00:00:00Z")
    html = index.render(index.collect(config), "2026-01-02T00:00:00Z")
    assert "feat/&lt;x&gt;" in html
    assert "<x>" not in html
    assert 'href="https://aaaaaaaa-demo.muelle.test"' in html
    assert 'href="https://github.com/owner/demo/commit/' + "a" * 40 + '"' in html
    assert "<code>aaaaaaa</code>" in html
    assert "1 deployment(s)" in html


def test_render_empty() -> None:
    assert "Nothing deployed to next or preview." in index.render([], "now")


def test_write_creates_both_files(config: Config) -> None:
    deployed(config, "demo", "next", "next", "main", "2026-01-01T00:00:00Z")
    index.write(config)
    out: Path = config.state_dir / "index"
    data = json.loads((out / "index.json").read_text())
    assert [e["env"] for e in data["entries"]] == ["next"]
    assert list(data["entries"][0]) == [
        "site",
        "env",
        "branch",
        "sha",
        "deployed_at",
        "slot",
        "url",
        "repo",
        "prod",
        "run_url",
    ]
    assert data["jobs"] == []
    assert (out / "index.html").stat().st_mode & 0o777 == 0o644


def test_html_escapes_everything_but_markup() -> None:
    raw = "<b>"
    trusted = index.Markup("<i>")
    rows = [index.Markup("<tr>"), index.Markup("</tr>")]
    assert index.html(t"{raw}{trusted}{rows}") == "&lt;b&gt;<i><tr></tr>"
    assert index.html(t"{['<s>']}") == "[&#x27;&lt;s&gt;&#x27;]"


NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
RUN = "https://github.com/owner/demo/actions/runs/7"
counter = iter(range(1000))


def job(
    config: Config,
    action: str,
    env: str,
    state: str,
    at: str = "2026-03-01T11:50:00Z",
    branch: str = "main",
    site: str = "demo",
    message: str = "",
) -> str:
    """A job as receive/worker leave it: job file plus status file, both stamped AT."""
    id = f"test-{next(counter):04d}"
    fields = {"site": site, "action": action, "env": env, "branch": branch, "sha": "b" * 40, "run_url": RUN}
    (config.state_dir / "jobs" / f"{id}.json").write_text(json.dumps({"id": id, **fields, "url": "", "created": at}))
    status = {"id": id, "state": state, "message": message, "url": "", "updated": at}
    (config.state_dir / "status" / f"{id}.json").write_text(json.dumps(status))
    return id


def shown(config: Config) -> list[tuple[str, str, str]]:
    return [(j["env"], j["branch"], j["state"]) for j in index.collect_jobs(config, NOW)]


def test_collect_jobs_shows_pending_and_failed_of_every_env(config: Config) -> None:
    job(config, "announce", "prod", "queued")
    job(config, "deploy", "next", "in_progress")
    job(config, "deploy", "preview", "failure", branch="feat/x", message="failed: sha256 mismatch")
    job(config, "deploy", "preview", "success", branch="feat/ok")
    assert sorted(shown(config)) == [
        ("next", "main", "in_progress"),
        ("preview", "feat/x", "failure"),
        ("prod", "main", "queued"),
    ]
    j = index.collect_jobs(config, NOW)[0]
    assert (j["run_url"], j["repo"], j["prod"]) == (RUN, "owner/demo", "https://demo.test")


def test_collect_jobs_newest_job_per_env_and_branch_wins(config: Config) -> None:
    job(config, "deploy", "next", "failure", at="2026-03-01T10:00:00Z")
    job(config, "deploy", "next", "success", at="2026-03-01T11:00:00Z", branch="other")
    job(config, "announce", "preview", "queued", at="2026-03-01T11:00:00Z", branch="feat/x")
    job(config, "deploy", "preview", "success", at="2026-03-01T11:10:00Z", branch="feat/x")
    job(config, "deploy", "preview", "failure", at="2026-03-01T10:00:00Z", branch="gone")
    job(config, "teardown", "preview", "success", at="2026-03-01T11:00:00Z", branch="gone")
    job(config, "deploy", "preview", "failure", at="2026-03-01T10:00:00Z", branch="feat/y")
    job(config, "announce", "preview", "queued", at="2026-03-01T11:30:00Z", branch="feat/y")
    assert shown(config) == [("preview", "feat/y", "queued")]


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_jobs_orders_jobs_of_the_same_second_by_action(reverse: bool) -> None:
    # announce, abort and deploy of one run, all within a second: listed in either order
    at = "2026-03-01T11:00:00Z"
    jobs = [
        {"site": "demo", "env": "preview", "branch": "feat/x", "action": action, "created": at, "updated": at}
        for action in ("announce", "abort", "deploy")
    ]
    for n in range(1, 4):
        given = jobs[:n][::-1] if reverse else jobs[:n]
        [latest] = index.latest_jobs(given).values()
        assert latest["action"] == jobs[n - 1]["action"]


def test_collect_jobs_drops_old_and_foreign_jobs(config: Config) -> None:
    job(config, "deploy", "next", "in_progress", at="2026-03-01T10:59:00Z")
    job(config, "deploy", "preview", "failure", at="2026-02-22T11:59:00Z", branch="old")
    job(config, "deploy", "preview", "failure", site="gone")
    job(config, "teardown", "preview", "failure", branch="td")
    (config.state_dir / "status" / "broken.json").write_text("{")
    assert shown(config) == []


def test_render_jobs(config: Config) -> None:
    job(config, "announce", "preview", "queued", branch="feat/<b>")
    job(config, "deploy", "next", "failure", message="failed: <boom>")
    html = index.render([], "now", index.collect_jobs(config, NOW))
    assert "Nothing deployed" not in html
    assert "building" in html
    assert "feat/&lt;b&gt;" in html
    assert '<span class="failed">failed: &lt;boom&gt;</span>' in html
    assert f'href="{RUN}"' in html
    assert "1 in progress &middot; 1 failed" in html
    assert '<meta http-equiv="refresh" content="15">' in html


def test_render_refreshes_only_while_pending(config: Config) -> None:
    job(config, "deploy", "next", "failure", message="failed: x")
    html = index.render([], "now", index.collect_jobs(config, NOW))
    assert "http-equiv" not in html
    assert "deploying" not in html


def test_render_links_run_of_deployed_entry(config: Config) -> None:
    deployed(config, "demo", "next", "next", "main", "2026-01-01T00:00:00Z")
    meta = config.base_dir / "demo" / "next" / "meta.json"
    meta.write_text(json.dumps(json.loads(meta.read_text()) | {"run_url": RUN}))
    assert f'href="{RUN}"' in index.render(index.collect(config), "now")
